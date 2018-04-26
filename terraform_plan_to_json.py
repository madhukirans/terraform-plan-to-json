#!/usr/bin/python

import re
import json
import collections
import argparse
import pprint
import logging

Action = {}
Action["CREATE"] = "create"
Action["DESTROY"] = "destroy"
Action["REPLACE"] = "replace"
Action["UPDATE"] = "update"
Action["READ"] = "read"

AttributeValueType = {}
AttributeValueType["UNKNOWN"] = "unknown"
AttributeValueType["STRING"] = "string"
AttributeValueType["COMPUTED"] = "computed"

ACTION_MAPPING = {}
ACTION_MAPPING['+'] = Action["CREATE"]
ACTION_MAPPING['-'] = Action["DESTROY"]
ACTION_MAPPING['-/+'] = Action["REPLACE"]
ACTION_MAPPING['~'] = Action["UPDATE"]
ACTION_MAPPING['<='] = Action["READ"]

logger = logging.getLogger()
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
logger.addHandler(ch)

class JsonSerializable(object):
    def toJson(self):
        return json.dumps(self.__dict__)

    def __repr__(self):
        return self.toJson()

class ChangeClass(JsonSerializable):
    def __init__(self, action, name, type, changed_attributes, new_resource_required):
        self.action = action
        self.name = name
        self.type = type
        self.changed_attributes = changed_attributes
        self.new_resource_required = new_resource_required
    changed_attributes = collections.OrderedDict()

class ResultOldNewClass(JsonSerializable):
    def __init__(self, oldObj, newObj, forcesNewResource):
        if (oldObj != None):
            self.old = oldObj
        if (newObj != None):
            self.new = newObj
        if (forcesNewResource != None):
            self.forcesNewResource = forcesNewResource

class ResultClass(JsonSerializable):
    def __init__(self, errors, changed_resources, changed_data_sources):
        self.errors = errors
        self.changed_resources = changed_resources
        self.changed_data_sources = changed_data_sources

    errors = []
    changed_resources = []
    changed_data_sources = []

class JSonLeafClass(JsonSerializable):
    def __init__(self, type, value):
        if (type != None):
            self.type = type
        if (value != None):
            self.value = value

def ends_with(str, search):
    strLen = len(str)
    return str[strLen - len(search):] == search

def parse_action_line(line, action, result):
    """
    Parse a line that looks similar to a resource or data source.

    Example line for resource:
    -/+ aws_ecs_task_definition.sample_app (new resource required)

    Example line for data source:
    <= data.external.ecr_image_digests

    :param line current line within stdout text
    :param action the pre-determined action which was found by looking at start of line
    :param result an object that collects changed data sources, changed resources, and errors
    :return an object that identifies a changed resource or data sources
    """

    # start position is after the action symbol
    # For example, we move past "-/+ " (4 characters)
    #id: "ocid1.instance.oc1.iad.aaaa.." => <computed> (forces new resource)
    ACTION_LINE_REGEX = '(data\.)?([^.]+)\.([^ ]+)( \(new resource required\))?$'
    match = re.match(ACTION_LINE_REGEX, line[4:])

    if (match is None):
        result.errors.append({
            'code': 'UNABLE_TO_PARSE_CHANGE_LINE',
            'message': 'Unable to parse "' + line + '" (ignoring)'
        })
        return None

    _, data_source_str, type, name, new_resource_required, _  = re.split(ACTION_LINE_REGEX, line[4:])
    change = ChangeClass(action, name, type, collections.OrderedDict(), new_resource_required)
    change.changed_attributes = collections.OrderedDict()
    change.new_resource_required = False

    if not (data_source_str is None):
        result.changed_data_sources.append(change)
    else:
        if not (new_resource_required is None):
            change.new_resource_required = True
        result.changed_resources.append(change)

    return change

def find_pos_of_next_non_space_char(str, from_index):
    """
       Find the position of next non-space character or -1 if we didn't
       find a non-space character
       @param str a string
       @param fromIndex the starting position
       @return the next position within string that is non-space character or -1 if not found
    """
    pos = from_index
    end = len(str)
    while (pos < end):
        if (str[pos] != ' '):
            return pos

        pos = pos + 1
    return -1


def parse_attribute_line(line, last_change, result):
    """
    Parses a line that we think looks like an attribute because it starts
    with six spaces and then a non-space character.

    line - the line that looks like an attribute change
    lastChange - the change object for resource or data source that will hold attribute
    errors - an array that will collect errors
    """
    start_pos = 6
    name_end_pos = line.find(':', start_pos + 1)

    if (name_end_pos == -1):
        result.errors.appned({
            'code': 'UNABLE_TO_PARSE_ATTRIBUTE_NAME',
            'message': 'Attribute name not found on line "' + line + '" (ignored)'
        })
        return

    oldObj = None
    newObj = None
    forces_new_resource = None
    name = line[start_pos: name_end_pos]

    start_pos = find_pos_of_next_non_space_char(line, name_end_pos + 1)

    if (start_pos != -1):
        firstObj, first_value_end_pos = parse_value(line, start_pos, result)
        start_pos = first_value_end_pos + 1
        ols_new_sep = ' => '
        if (line[start_pos: start_pos + len(ols_new_sep)] == ols_new_sep):
            # there is a " => " so we have an old and new value
            newObj, endPos = parse_value(line, start_pos + len(ols_new_sep), result)
            oldObj = firstObj
        else:
            # there is no " => " so we only have a new value
            newObj = firstObj

        if (ends_with(line, ' (forces new resource)')):
            forces_new_resource = True

    result1 = ResultOldNewClass(oldObj, newObj, forces_new_resource)
    last_change.changed_attributes[name] = result1


"""
   [findStringEndDelimiterPos description]
   @param str the string that contains a quoted string that needs to be parsed
   @param fromIndex the position of the first character after the `"` character
   @return the position of the ending `"` or -1 if the string is unterminated
"""


def find_string_end_delimiter_pos(str, from_index):

    pos = from_index
    escaped = False
    end = len(str)
    while (pos < end):
        if (escaped):
            escaped = False
        else:
            if (str[pos] == '"'):
                return pos
            elif (str[pos] == '\\'):
                escaped = True

        pos = pos + 1

    return -1


def read_upto_char(str, from_index, terminator_char):
    """
    Read ahead in a string until we ecounter the given terminator character or end of string
    :param str a string
    :param fromIndex the starting position
    :param terminatorChar a terminator string of length 1
    :return the substring from `fromIndex` up to (but not including) the terminator character
    """
    return str[from_index: str.find(terminator_char, from_index)]


def parse_value(line, from_index, result):
    """
    This function is used to parse values such as:
    `<computed>`
    `"arn:aws:iam::123123123123:role/SampleApp"`

    :param line: - the line read from terraform stdout content
    :param from_index - the starting position of a _value_
    :param result - an array that collects errors
    :return an array with two items (first item is result value object and second item is the end position of the value)
    """
    foundDelimiter = line[from_index]
    endPos = type = value = None
    if (foundDelimiter == '"'):
        endPos = find_string_end_delimiter_pos(line, from_index + 1)
        if (endPos == -1):
            endPos = len(line)
            value = line[from_index: endPos]
            result.errors.append({
                'code': 'UNTERMINATED_STRING',
                'message': 'Unterminated string on line "' + line + '"'
            })
        else:
            type = AttributeValueType["STRING"]
            value = line[from_index + 1: endPos]

    elif (foundDelimiter == '<'):
        contents = read_upto_char(line, from_index+ 1, '>')

        if (contents is None):
            # we did not find the terminator character
            value = line[from_index:]
            endPos = len(line)
        else:
            if (contents == 'computed'):
                type = AttributeValueType["COMPUTED"]
            else:
                print (line[from_index + 1: from_index + len(contents)])
                value = line[from_index + 1: from_index + len(contents)]
            endPos = from_index + len(contents) + 1
    else:
        value = line[from_index:]
        endPos = from_index + len(value)

    if (type is None):
        type = AttributeValueType["UNKNOWN"]

    result1 = JSonLeafClass(type, value)
    return result1, endPos


def parse_stdout_to_json(logOutput):
    # Remve NonAnsi Charcters
    logOutput = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', logOutput)
    # print logOutput
    logOutput = re.sub('/\r\n/g', '\n', logOutput)

    result = ResultClass([], [], [])
    lastChange = None

    CONTENT_START_STRING = '\nTerraform will perform the following actions:\n'
    startPos = logOutput.find(CONTENT_START_STRING)
    if (startPos >= 0):
        startPos = startPos + len(CONTENT_START_STRING)
    else:
        result.errors.append(({
            'code': 'UNABLE_TO_FIND_STARTING_POSITION_WITHIN_STDOUT',
            'message': 'Did not find magic starting string: ' + CONTENT_START_STRING
        }))
        return result

    endPos = logOutput.index('\nPlan:', startPos)
    if (endPos == -1):
        result.errors.append({
            'code': 'UNABLE_TO_FIND_ENDING_POSITION_WITHIN_STDOUT',
            'message': 'Did not find magic ending string: \\nPlan:'
        })
        return result

    changesText = logOutput[startPos: endPos]
    lines = changesText.split('\n')

    for line in lines:
        # print line
        if (len(line) == 0):
            # blank lines separate each resource / data source.
            lastChange = None
            continue

        possibleActionSymbol = line[0: 3].strip()
        spacePos = possibleActionSymbol.rfind(' ')
        if (spacePos != -1):
            possibleActionSymbol = possibleActionSymbol[0:spacePos]

        action = None
        if possibleActionSymbol != '':
            action = ACTION_MAPPING[possibleActionSymbol]

        ATTRIBUTE_LINE_REGEX = '^ {6}[^ ]'
        match = re.match(ATTRIBUTE_LINE_REGEX, line)

        if not (action is None):
            # line starts with an action symbol so it will be followed by
            # something like "data.external.ecr_image_digests"
            # or "aws_ecs_task_definition.sample_app (new resource required)"
            lastChange = parse_action_line(line, action, result)

        elif not (match is None):
            if not (lastChange is None):
                parse_attribute_line(line, lastChange, result)
            else:
                # This line looks like an attribute but there is no resource
                # 3 or data source that will hold it.
                print ('ORPHAN_ATTRIBUTE_LINE', line)
                result.errors.append({
                    'code': 'ORPHAN_ATTRIBUTE_LINE',
                    'message': 'Attribute line "' + line + '" is not associated with a data source or resource (ignoring)'
                })
        else:
            # We don't recognize what this line is....
            print ('UNABLE_TO_PARSE_LINE', line)
            result.errors.append({
                'code': 'UNABLE_TO_PARSE_LINE',
                'message': 'Unable to parse "' + line + '" (ignoring)'
            })

    def dumper(obj):
        try:
            return obj.toJSON()
        except:
            return obj.__dict__

    jsonstr = json.dumps(result.__dict__, indent=2, default=dumper)
    return jsonstr

def get_changed_resources(json_str):
    json_obj = json.loads(str(json_str))
    return json_obj["changed_resources"]

def pretty_print(json_obj):
    if isinstance(json_obj, list):
        for i in json_obj:
            #print(type(i))
            #parsed = json.loads(i)
            print (json.dumps(i, indent=4, sort_keys=False))
    else:
        parsed = json.loads(json_obj)
        print (json.dumps(parsed, indent=4, sort_keys=False))



def main():
    parser = argparse.ArgumentParser(description='Terraform  plan output to JSon converter')
    parser.add_argument('--input', type=str, default=None, help='Terraform Plan input file')
    parser.add_argument('--output', type=str, default=None, help='JSon Output file')
    args = parser.parse_args()

    file = open(args.input, 'r')
    output = file.read()
    file.close()

    obj = parse_stdout_to_json(output)

    obj1 = get_changed_resources(obj)

    file = open(args.output, 'w+')
    file.write(obj)
    file.close()

if __name__ == "__main__":
    main()


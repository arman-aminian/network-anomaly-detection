import json
import re


def parse_http_agent(http_agent):
    regex = r'(?<=\().+?(?=\))'
    in_bracket_dict_values = re.findall(regex, http_agent)
    result_values = []
    for value in in_bracket_dict_values:
        result_values.append(value.split('; '))
        http_agent = http_agent.replace(f' ({value})', '')

    result_values += http_agent.split()
    return result_values


def parse(line: str):
    line = line.replace('[[', '[').replace(']]', ']')
    normal_dict_keys = ('ip', 'status_code', 'request_length', 'request_time',)
    in_bracket_dict_keys = ('datetime', 'method_and_url', 'http_user_agent')
    result_dict = dict()
    regex = r'(?<=\[).+?(?=\])'
    in_bracket_dict_values = re.findall(regex, line)
    for i in range(len(in_bracket_dict_keys)):
        result_dict[in_bracket_dict_keys[i]] = in_bracket_dict_values[i]
        line = line.replace(f' [{in_bracket_dict_values[i]}]', '')
    normal_dict_values = line.split()
    for i in range(len(normal_dict_keys)):
        result_dict[normal_dict_keys[i]] = normal_dict_values[i]

    result_dict['http_method'], result_dict['url'] = result_dict.pop('method_and_url').split()
    result_dict['http_user_agent'] = parse_http_agent(result_dict['http_user_agent'])
    return result_dict


if __name__ == '__main__':
    with open("output.log") as f:
        lines = f.readlines()
    parsed_lines = list(map(parse, lines))
    with open('result.json', 'w') as f:
        json.dump(parsed_lines, f)

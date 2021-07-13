import json
import re

incoming_req_hashtable = {}

# Press the green button in the gutter to run the script.
def load_model(param):
    pass

def parse_http_agent(http_agent):
    return http_agent


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


def run_model():
    model = load_model('./MODEL_PATH')

def predict(model, x_test):
    scores = model.predict(x_test)
    return scores

def get_req_unique_str(req):
    return str(req.http_user_agent + ' - ' + req.ip)

def preprocess(session):
    res = pd.DataFrame(columns=['head_freq', 'req_num', 'img_freq',
                                'page_freq', 'status_4xx_freq', 'max_click_rate',
                                'has_robots', 'duration'])
    temp = []
    for i in range(len(session)):
        if len(temp) > 0 and (session.datetime.iloc[i] - session.datetime.iloc[i - 1]).seconds > SESSION_THRESHOLD:
            temp = pd.DataFrame(temp)
            res = res.append({'head_freq': get_head_freq(temp),
                              'req_num': len(temp),
                              'img_freq': get_image_freq(temp),
                              'page_freq': get_page_freq(temp),
                              'status_4xx_freq': get_4xx_freq(temp),
                              'max_click_rate': get_max_click_rate(temp),
                              'has_robots': has_robots_req(temp),
                              'duration': get_duration(temp),
                              'is_bot': is_bot(temp)
                              },
                             ignore_index=True)
            temp = []
        temp.append(session.iloc[i])
    temp = pd.DataFrame(temp)
    res = res.append({'head_freq': get_head_freq(temp),
                      'req_num': len(temp),
                      'img_freq': get_image_freq(temp),
                      'page_freq': get_page_freq(temp),
                      'status_4xx_freq': get_4xx_freq(temp),
                      'max_click_rate': get_max_click_rate(temp),
                      'has_robots': has_robots_req(temp),
                      'duration': get_duration(temp),
                      'is_bot': is_bot(temp)
                      },
                     ignore_index=True)
    return res.iloc[-1]

def is_anomaly(model, req):
    parsed_lines = list(map(parse, req))[0]
    pd.DataFrame.from_dict([parsed_lines])

    if incoming_req_hashtable[get_req_unique_str(req)] is None:
        incoming_req_hashtable[get_req_unique_str(req)] = [req]
    elif len(incoming_req_hashtable[get_req_unique_str(req)]) >= 5:
        preprocessed = preprocess(incoming_req_hashtable[get_req_unique_str(req)])
        incoming_req_hashtable[get_req_unique_str(req)] = incoming_req_hashtable[get_req_unique_str(req)][-len(preprocessed):]

    else:
        incoming_req_hashtable[get_req_unique_str(req)].append(req)


if __name__ == '__main__':
    run_model()

# See PyCharm help at https://www.jetbrains.com/help/pycharm/

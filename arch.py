 # pip install pyyaml ua-parser user-agents

from redis import StrictRedis
import json
from flask import Flask, request


import numpy as np
np.random.seed(42)
import pandas as pd
from pandas import Timestamp
from user_agents import parse
import tensorflow as tf
tf.config.experimental_run_functions_eagerly(True)
tf.random.set_seed(42)
sess = tf.compat.v1.Session()
from sklearn.preprocessing import MinMaxScaler

from keras import regularizers
from keras import backend as K
from keras.models import Model, load_model
from keras.layers import Input, Dense
from keras.optimizers import RMSprop
from keras.callbacks import ModelCheckpoint, TensorBoard

import joblib
import argparse
import numpy as np
import matplotlib.pyplot as plt
import sys
from scipy.sparse import vstack, csc_matrix
from utils import dataLoading, aucPerformance, writeResults, get_data_from_svmlight_file
from sklearn.model_selection import train_test_split

import time
import re

MAX_INT = np.iinfo(np.int32).max
data_format = 0

def dev_network_d(input_shape):
    '''
    deeper network architecture with three hidden layers
    '''
    x_input = Input(shape=input_shape)
    intermediate = Dense(1000, activation='relu',
                kernel_regularizer=regularizers.l2(0.01), name = 'hl1')(x_input)
    intermediate = Dense(250, activation='relu',
                kernel_regularizer=regularizers.l2(0.01), name = 'hl2')(intermediate)
    intermediate = Dense(20, activation='relu',
                kernel_regularizer=regularizers.l2(0.01), name = 'hl3')(intermediate)
    intermediate = Dense(1, activation='linear', name = 'score')(intermediate)
    return Model(x_input, intermediate)

def dev_network_s(input_shape):
    '''
    network architecture with one hidden layer
    '''
    x_input = Input(shape=input_shape)
    intermediate = Dense(20, activation='relu', 
                kernel_regularizer=regularizers.l2(0.01), name = 'hl1')(x_input)
    intermediate = Dense(1, activation='linear',  name = 'score')(intermediate)    
    return Model(x_input, intermediate)

def dev_network_linear(input_shape):
    '''
    network architecture with no hidden layer, equivalent to linear mapping from
    raw inputs to anomaly scores
    '''    
    x_input = Input(shape=input_shape)
    intermediate = Dense(1, activation='linear',  name = 'score')(x_input)
    return Model(x_input, intermediate)

def deviation_loss(y_true, y_pred):
    '''
    z-score-based deviation loss
    '''    
    confidence_margin = 5.     
    ## size=5000 is the setting of l in algorithm 1 in the paper
    ref = K.variable(np.random.normal(loc = 0., scale= 1.0, size = 5000) , dtype='float32')
    dev = (y_pred - K.mean(ref)) / K.std(ref)
    inlier_loss = K.abs(dev) 
    outlier_loss = K.abs(K.maximum(confidence_margin - dev, 0.))
    return K.mean((1 - y_true) * inlier_loss + y_true * outlier_loss)


def deviation_network(input_shape, network_depth):
    '''
    construct the deviation network-based detection model
    '''
    if network_depth == 4:
        model = dev_network_d(input_shape)
    elif network_depth == 2:
        model = dev_network_s(input_shape)
    elif network_depth == 1:
        model = dev_network_linear(input_shape)
    else:
        sys.exit("The network depth is not set properly")
    rms = RMSprop(clipnorm=1.)
    model.compile(loss=deviation_loss, optimizer=rms)
    return model


def batch_generator_sup(x, outlier_indices, inlier_indices, batch_size, nb_batch, rng):
    """batch generator
    """
    rng = np.random.RandomState(rng.randint(MAX_INT, size = 1))
    counter = 0
    while 1:                
        if data_format == 0:
            ref, training_labels = input_batch_generation_sup(x, outlier_indices, inlier_indices, batch_size, rng)
        else:
            ref, training_labels = input_batch_generation_sup_sparse(x, outlier_indices, inlier_indices, batch_size, rng)
        counter += 1
        yield(ref, training_labels)
        if (counter > nb_batch):
            counter = 0
 
def input_batch_generation_sup(x_train, outlier_indices, inlier_indices, batch_size, rng):
    '''
    batchs of samples. This is for csv data.
    Alternates between positive and negative pairs.
    '''      
    dim = x_train.shape[1]
    ref = np.empty((batch_size, dim))    
    training_labels = []
    n_inliers = len(inlier_indices)
    n_outliers = len(outlier_indices)
    for i in range(batch_size):    
        if(i % 2 == 0):
            sid = rng.choice(n_inliers, 1)
            ref[i] = x_train[inlier_indices[sid]]
            training_labels += [0]
        else:
            sid = rng.choice(n_outliers, 1)
            ref[i] = x_train[outlier_indices[sid]]
            training_labels += [1]
    return np.array(ref), np.array(training_labels).astype(np.float32)

 
def input_batch_generation_sup_sparse(x_train, outlier_indices, inlier_indices, batch_size, rng):
    '''
    batchs of samples. This is for libsvm stored sparse data.
    Alternates between positive and negative pairs.
    '''      
    ref = np.empty((batch_size))    
    training_labels = []
    n_inliers = len(inlier_indices)
    n_outliers = len(outlier_indices)
    for i in range(batch_size):    
        if(i % 2 == 0):
            sid = rng.choice(n_inliers, 1)
            ref[i] = inlier_indices[sid]
            training_labels += [0]
        else:
            sid = rng.choice(n_outliers, 1)
            ref[i] = outlier_indices[sid]
            training_labels += [1]
    ref = x_train[ref, :].toarray()
    return ref, np.array(training_labels)


def load_model_weight_predict(model_name, input_shape, network_depth, x_test):
    '''
    load the saved weights to make predictions
    '''
    model = deviation_network(input_shape, network_depth)
    model.load_weights(model_name)
    scoring_network = Model(inputs=model.input, outputs=model.output)    
    
    if data_format == 0:
        scores = scoring_network.predict(x_test)
    else:
        data_size = x_test.shape[0]
        scores = np.zeros([data_size, 1])
        count = 512
        i = 0
        while i < data_size:
            subset = x_test[i:count].toarray()
            scores[i:count] = scoring_network.predict(subset)
            if i % 1024 == 0:
                print(i)
            i = count
            count += 512
            if count > data_size:
                count = data_size
        assert count == data_size
    return scores


def inject_noise_sparse(seed, n_out, random_seed):  
    '''
    add anomalies to training data to replicate anomaly contaminated data sets.
    we randomly swape 5% features of anomalies to avoid duplicate contaminated anomalies.
    This is for sparse data.
    '''
    rng = np.random.RandomState(random_seed) 
    n_sample, dim = seed.shape
    swap_ratio = 0.05
    n_swap_feat = int(swap_ratio * dim)
    seed = seed.tocsc()
    noise = csc_matrix((n_out, dim))
    print(noise.shape)
    for i in np.arange(n_out):
        outlier_idx = rng.choice(n_sample, 2, replace = False)
        o1 = seed[outlier_idx[0]]
        o2 = seed[outlier_idx[1]]
        swap_feats = rng.choice(dim, n_swap_feat, replace = False)
        noise[i] = o1.copy()
        noise[i, swap_feats] = o2[0, swap_feats]
    return noise.tocsr()

def inject_noise(seed, n_out, random_seed):   
    '''
    add anomalies to training data to replicate anomaly contaminated data sets.
    we randomly swape 5% features of anomalies to avoid duplicate contaminated anomalies.
    this is for dense data
    '''  
    rng = np.random.RandomState(random_seed) 
    n_sample, dim = seed.shape
    swap_ratio = 0.05
    n_swap_feat = int(swap_ratio * dim)
    noise = np.empty((n_out, dim))
    for i in np.arange(n_out):
        outlier_idx = rng.choice(n_sample, 2, replace = False)
        o1 = seed[outlier_idx[0]]
        o2 = seed[outlier_idx[1]]
        swap_feats = rng.choice(dim, n_swap_feat, replace = False)
        noise[i] = o1.copy()
        noise[i, swap_feats] = o2[swap_feats]
    return noise

def run_devnet(args):
    names = args.data_set.split(',')
    names = ['prepared_ds']
    network_depth = int(args.network_depth)
    random_seed = args.ramdn_seed
    for nm in names:
        runs = args.runs
        rauc = np.zeros(runs)
        ap = np.zeros(runs)  
        filename = nm.strip()
        global data_format
        data_format = int(args.data_format)
        if data_format == 0:
            x, labels = dataLoading(args.input_path + filename + ".csv")
        else:
            x, labels = get_data_from_svmlight_file(args.input_path + filename + ".svm")
            x = x.tocsr()    
        outlier_indices = np.where(labels == 1)[0]
        outliers = x[outlier_indices]  
        n_outliers_org = outliers.shape[0]   
        
        train_time = 0
        test_time = 0
        for i in np.arange(runs):  
            # x_train, x_test, y_train, y_test = train_test_split(x, labels, test_size=0.2, random_state=42, stratify = labels)
            x_train = x
            y_train = labels
            y_train = np.array(y_train)
            # y_test = np.array(y_test)
            print(filename + ': round ' + str(i))
            outlier_indices = np.where(y_train == 1)[0]
            inlier_indices = np.where(y_train == 0)[0]
            n_outliers = len(outlier_indices)
            print("Original training size: %d, No. outliers: %d" % (x_train.shape[0], n_outliers))
            
            n_noise  = len(np.where(y_train == 0)[0]) * args.cont_rate / (1. - args.cont_rate)
            n_noise = int(n_noise)                
            
            rng = np.random.RandomState(random_seed)  
            if data_format == 0:                
                if n_outliers > args.known_outliers:
                    mn = n_outliers - args.known_outliers
                    remove_idx = rng.choice(outlier_indices, mn, replace=False)            
                    x_train = np.delete(x_train, remove_idx, axis=0)
                    y_train = np.delete(y_train, remove_idx, axis=0)
                
                noises = inject_noise(outliers, n_noise, random_seed)
                x_train = np.append(x_train, noises, axis = 0)
                y_train = np.append(y_train, np.zeros((noises.shape[0], 1)))
            
            else:
                if n_outliers > args.known_outliers:
                    mn = n_outliers - args.known_outliers
                    remove_idx = rng.choice(outlier_indices, mn, replace=False)        
                    retain_idx = set(np.arange(x_train.shape[0])) - set(remove_idx)
                    retain_idx = list(retain_idx)
                    x_train = x_train[retain_idx]
                    y_train = y_train[retain_idx]                               
                
                noises = inject_noise_sparse(outliers, n_noise, random_seed)
                x_train = vstack([x_train, noises])
                y_train = np.append(y_train, np.zeros((noises.shape[0], 1)))
            
            outlier_indices = np.where(y_train == 1)[0]
            inlier_indices = np.where(y_train == 0)[0]
            print(y_train.shape[0], outlier_indices.shape[0], inlier_indices.shape[0], n_noise)
            input_shape = x_train.shape[1:]
            n_samples_trn = x_train.shape[0]
            n_outliers = len(outlier_indices)            
            print("Training data size: %d, No. outliers: %d" % (x_train.shape[0], n_outliers))
            
            
            start_time = time.time() 
            input_shape = x_train.shape[1:]
            epochs = args.epochs
            batch_size = args.batch_size    
            nb_batch = args.nb_batch  
            model = deviation_network(input_shape, network_depth)
            print(model.summary())  
            model_name = "./model/devnet_"  + filename + "_" + str(args.cont_rate) + "cr_"  + str(args.batch_size) +"bs_" + str(args.known_outliers) + "ko_" + str(network_depth) +"d.h5"
            checkpointer = ModelCheckpoint(model_name, monitor='loss', verbose=0,
                                           save_best_only = True, save_weights_only = True)            
            
            model.fit_generator(batch_generator_sup(x_train, outlier_indices, inlier_indices, batch_size, nb_batch, rng),
                                          steps_per_epoch = nb_batch,
                                          epochs = epochs,
                                          callbacks=[checkpointer])   
            train_time += time.time() - start_time
            
            start_time = time.time() 
            # scores = load_model_weight_predict(model_name, input_shape, network_depth, x_test)
            test_time += time.time() - start_time
            # rauc[i], ap[i] = aucPerformance(scores, y_test)     
        
        mean_auc = np.mean(rauc)
        std_auc = np.std(rauc)
        mean_aucpr = np.mean(ap)
        std_aucpr = np.std(ap)
        train_time = train_time/runs
        test_time = test_time/runs
        print("average AUC-ROC: %.4f, average AUC-PR: %.4f" % (mean_auc, mean_aucpr))    
        print("average runtime: %.4f seconds" % (train_time + test_time))
        writeResults(filename+'_'+str(network_depth), x.shape[0], x.shape[1], n_samples_trn, n_outliers_org, n_outliers,
                     network_depth, mean_auc, mean_aucpr, std_auc, std_aucpr, train_time, test_time, path=args.output)

def load_model_weight_predict(model_name, input_shape, network_depth, x_test):
    '''
    load the saved weights to make predictions
    '''
    model = deviation_network(input_shape, network_depth)
    model.load_weights(model_name)
    scoring_network = Model(inputs=model.input, outputs=model.output)    
    scores = scoring_network.predict(x_test)



def parse_http_agent(http_agent):
    return http_agent


def req_parse(line: str):
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



"""# Helper Functions"""

def get_root(s):
    if s.startswith('/') and len(s) > 1:
        s = s[1:]
    if len(s) > 1:
        s = s[:s.find('/')]
    return s

def get_path_roots(urls, min_samples_per_root=1000):
    t = [get_root(s) for s in urls]

    t = ['ROBOTS' if 'robots' in s else s for s in t]
    t = ['NUM' if s.isnumeric() else s for s in t]

    t2 = pd.Series(t).value_counts()[pd.Series(t).value_counts().values > min_samples_per_root]
    if 'ROBOTS' not in t2:
        roots = np.concatenate((t2.index.values, ['ROBOTS']))
        values = np.concatenate((t2.values, [pd.Series(t).value_counts()['ROBOTS']]))
    else:
        roots = np.array(t2.index.values)
        values = np.array(t2.values)
    return roots, values

def convert_urls_to_roots(urls, min_samples_per_root=1000):
    t = [get_root(s) for s in urls]

    t = ['ROBOTS' if 'robots' in s else s for s in t]
    t = ['NUM' if s.isnumeric() else s for s in t]

    t2 = pd.Series(t).value_counts()[pd.Series(t).value_counts().values > MIN_PATH_ROOT_SAMPLE]
    if 'ROBOTS' not in t2:
        roots = np.concatenate((t2.index.values, ['ROBOTS']))
    else:
        roots = np.array(t2.index.values)
    t = pd.Series([s if s in roots else 'OTHER' for s in t])
    return t

def get_categorical_status_code_counts(status_codes):
    status_counts = np.zeros(5)
    for i in range(status_codes.nunique()):
        status_counts[int(np.floor(int(status_codes.value_counts().index[i]) / 100) - 1)] += status_codes.value_counts().values[i]
    return status_counts

def isnumeric(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

"""# Session helper functions"""

def get_max_click_rate(session):
    m = 0
    session = session[session.url.apply(lambda x: 'pages' in x)]
    for l in session.datetime:
        m = max(m, len(session[(session.datetime >= l) &
                               (session.datetime <= l + timedelta(seconds=TIME_WINDOW))]))
    return m

def get_duration(session):
    return (Timestamp(session.datetime.iloc[-1]) - Timestamp(session.datetime.iloc[0])).seconds

def get_image_freq(session):
    t = [get_root(s) for s in session.url]
    return t.count('images') / len(t)

def get_4xx_freq(session):
    status_counts = get_categorical_status_code_counts(session.status_code)
    return status_counts[3] / len(session)

def get_page_freq(session):
    t = [get_root(s) for s in session.url]
    return (t.count('pages') + t.count('php') + t.count('asp')) / len(t)

def get_head_freq(session):
    return len(session[session.http_method == 'Head']) / len(session)

def has_robots_req(session):
    t = ['robots' in s for s in session.url]
    return int(sum(t) > 0)

def is_bot(session):
    user_agent = parse(session.http_user_agent.iloc[0])
    return int(user_agent.is_bot)

"""# Preprocess"""

train_data = pd.read_csv('./dataset/training_data.csv', index_col=0)
# train_data.head()

scalar = MinMaxScaler().fit(train_data)

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
    
    # res = res[res.req_num >= 5]
    if len(res) == 0:
      return -1

    res['req_freq'] = res.req_num / (res.duration + 0.001)

    res = scalar.transform(res)
    res = pd.DataFrame(res)
    
    return res.iloc[-1]

lines = ['207.213.193.143 [2021-5-12T5:6:0.0+0430] [Get /cdn/profiles/1026106239] 304 0 [[Googlebot-Image/1.0]] 32']
parsed_lines = list(map(req_parse, lines))[0]

parsed_pd = pd.DataFrame.from_dict([parsed_lines])

preprocessed_data = preprocess(parsed_pd)

preprocessed_data = preprocessed_data.astype(np.float32)
preprocessed_data

"""# Load trained Model"""

def load_model_weight(model_name, input_shape, network_depth, x_test):
    model = deviation_network(input_shape, network_depth)
    model.load_weights(model_name)
    scoring_network = Model(inputs=model.input, outputs=model.output)    
    return scoring_network

# model = load_model_weight('./model/devnet_prepared_ds_0.02cr_512bs_690ko_2d-2.h5', input_shape, 2, preprocessed_data.values.reshape(1, 10))

# model.predict(preprocessed_data.values.reshape(1, 10))

def is_anomaly( model, request, threshold ):
  lines = [request]
  parsed_lines = list(map(req_parse, lines))[0]
  parsed_pd = pd.DataFrame.from_dict([parsed_lines])
  preprocessed_data = preprocess(parsed_pd)
  preprocessed_data = preprocessed_data.astype(np.float32)
  score = model.predict(preprocessed_data.values.reshape(1, 10))
  return score[0,0] > threshold

# x = '207.213.193.143 [2021-5-12T5:6:0.0+0430] [Get /cdn/profiles/1026106239] 304 0 [[Googlebot-Image/1.0]] 32'
# is_anomaly(model, x, 5)

"""# System Architecture"""

# incoming_req_hashtable = {}
# discovered_robots = set()
input_shape = (10,)

model = load_model_weight('./model/devnet_prepared_ds_0.02cr_512bs_690ko_2d-2.h5', input_shape, 2, preprocessed_data.values.reshape(1, 10))

def get_req_unique_str( req ):
    return str(req.http_user_agent + ' - ' + req.ip)

def request_validate( req ):
  lines = [req]
  parsed_lines = list(map(req_parse, lines))[0]
  req_pd = pd.DataFrame.from_dict([parsed_lines])
  redis_key = get_req_unique_str(req_pd)

  discovered_robots = set(get_list_from_redis('discovered_robots', list()))
  if redis_key in discovered_robots:
    return False

  incoming_req = get_list_from_redis(redis_key, list())
  if len(incoming_req) >= 5:
    anomaly = is_anomaly(model, req, 5.1)
    if anomaly:  
      discovered_robots.add(redis_key)
      add_list_to_redis('discovered_robots', list(discovered_robots))
    return ~anomaly
  incoming_req.append(req_pd)
  add_list_to_redis(redis_key, incoming_req)
  return True


  # if get_req_unique_str(req_pd) not in incoming_req_hashtable.keys():
  #   incoming_req_hashtable[get_req_unique_str(req_pd)] = [req_pd]
  # elif len(incoming_req_hashtable[get_req_unique_str(req_pd)]) >= 5:
  #   anomaly = is_anomaly(model, req, 5.1)
  #   if anomaly:
  #     discovered_robots.add(get_req_unique_str(req_pd))
  #   return ~anomaly
  #   # incoming_req_hashtable[get_req_unique_str(req_pd)] = incoming_req_hashtable[get_req_unique_str(req_pd)][-len(preprocessed):]
  # else:
  #   incoming_req_hashtable[get_req_unique_str(req_pd)].append(req_pd)
  # return True

x = '217.213.193.143 [2021-5-12T5:6:0.0+0430] [Get /cdn/profiles/robots.txt] 304 0 [[Googlebot-Image/1.0]] 32'

request_validate(x)


redis_cli = StrictRedis(host='localhost', port=6379, db=0)

def add_list_to_redis(key, value):
    redis_cli.set(key, json.dumps(value))


def get_list_from_redis(key, default=None):
    value = redis_cli.get(key)
    if value is None:
        return default
    return json.loads(value)


app = Flask(__name__)


@app.route('/predict', methods=['POST'])
def predict():
    request_json = request.json()
    http_req_log = request_json['http_req_log']
    response = request_validate(http_req_log)
    return {'response': response}


if __name__ == '__main__':
    app.run()



# discovered_robots


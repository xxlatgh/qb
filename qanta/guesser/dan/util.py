from typing import List, Set, Dict
import os
import pickle
import tensorflow as tf
import numpy as np

from qanta import logging
from qanta.util.io import safe_open

log = logging.get(__name__)


GLOVE_WE = 'data/external/deep/glove.6B.300d.txt'


def make_layer(postfix: str, in_tensor, n_out, op,
               n_in=None, dropout_prob=None, batch_norm=False, batch_is_training=None):
    with tf.variable_scope('layer' + postfix):
        if batch_norm and batch_is_training is None:
            raise ValueError('if using batch norm then passing a training placeholder is required')
        w = tf.get_variable('w', (in_tensor.get_shape()[1] if n_in is None else n_in, n_out),
                            dtype=tf.float32)
        if dropout_prob is not None:
            w = tf.nn.dropout(w, keep_prob=1 - dropout_prob)
        b = tf.get_variable('b', n_out, dtype=tf.float32)
        out = tf.matmul(in_tensor, w) + b
        if batch_norm:
            out = tf.contrib.layers.batch_norm(
                out, center=True, scale=True, is_training=batch_is_training, scope='bn', fused=True)
        out = (out if op is None else op(out))
        return out, w, b


def parametric_relu(x):
    alphas = tf.get_variable(
        'alpha',
        x.get_shape()[-1],
        initializer=tf.constant_initializer(0.0),
        dtype=tf.float32
    )
    pos = tf.nn.relu(x)
    neg = alphas * (x - abs(x)) * 0.5

    return pos + neg


def create_embeddings(vocab: Set[str]):
    embeddings = []
    embedding_lookup = {}
    with open(GLOVE_WE) as f:
        i = 0
        for l in f:
            splits = l.split()
            word = splits[0]
            if word in vocab:
                emb = [float(n) for n in splits[1:]]
                embeddings.append(emb)
                embedding_lookup[word] = i
                i += 1
        embeddings = np.array(embeddings)
        mean_embedding = embeddings.mean(axis=0)
        embed_with_unk = np.vstack([embeddings, mean_embedding])
        embedding_lookup['UNK'] = i
        return embed_with_unk, embedding_lookup


def load_embeddings(tmp_embedding_file, embedding_file, vocab=None, root_directory=''):
    if os.path.exists(tmp_embedding_file):
        log.info('Loading word embeddings from tmp cache')
        with safe_open(tmp_embedding_file, 'rb') as f:
            return pickle.load(f)
    elif os.path.exists(os.path.join(root_directory, embedding_file)):
        log.info('Loading word embeddings from restored cache')
        with safe_open(os.path.join(root_directory, embedding_file), 'rb') as f:
            return pickle.load(f)
    else:
        if vocab is None:
            raise ValueError('To create fresh embeddings a vocab is needed')
        with safe_open(tmp_embedding_file, 'wb') as f:
            log.info('Creating word embeddings and saving to cache')
            embed_and_lookup = create_embeddings(vocab)
            pickle.dump(embed_and_lookup, f)
            return embed_and_lookup


def convert_text_to_embeddings_indices(words: List[str], embedding_lookup: Dict[str, int]):
    w_indices = []
    for w in words:
        if w in embedding_lookup:
            w_indices.append(embedding_lookup[w])
        else:
            w_indices.append(embedding_lookup['UNK'])
    return w_indices


def compute_n_classes(labels: List[str]):
    return len(set(labels))


def compute_max_len(x_data: List[List[int]]):
    return max(len(x) for x in x_data)


def tf_format(x_data: List[List[int]], max_len: int, zero_index: int):
    """
    Pad with elements until it has max_len or shorten it until it has max_len. When padding insert
    the zero index so it doesn't contribute anything
    :param x_data:
    :param max_len:
    :return:
    """
    for i in range(len(x_data)):
        row = x_data[i]
        while len(row) < max_len:
            row.append(zero_index)
        x_data[i] = x_data[i][:max_len]
    return x_data


def create_batches(batch_size,
                   x_data: np.ndarray, y_data: np.ndarray, x_lengths: np.ndarray,
                   ans_type_labels, category_labels, gender_labels,
                   pad=False, shuffle=True):
    if type(x_data) != np.ndarray or type(y_data) != np.ndarray:
        raise ValueError('x and y must be numpy arrays')
    if len(x_data) != len(y_data):
        raise ValueError('x and y must have the same dimension')
    n = len(x_data)
    order = list(range(n))
    if shuffle:
        np.random.shuffle(order)
    for i in range(0, n, batch_size):
        if len(order[i:i + batch_size]) == batch_size:
            x_batch = x_data[order[i:i + batch_size]]
            y_batch = y_data[order[i:i + batch_size]]
            x_batch_lengths = x_lengths[order[i:i + batch_size]]
            ans_type_batch = ans_type_labels[order[i: i + batch_size]]
            category_batch = category_labels[order[i: i + batch_size]]
            gender_batch = gender_labels[order[i: i + batch_size]]
            yield x_batch, y_batch, x_batch_lengths, ans_type_batch, category_batch, gender_batch
        elif pad:
            size = len(order[i:i + batch_size])
            x_batch = np.vstack((
                x_data[order[i:i + batch_size]],
                np.zeros((batch_size - size, x_data.shape[1])))
            )
            y_batch = np.hstack((
                y_data[order[i:i + batch_size]],
                np.zeros((batch_size - size,)))
            )
            x_batch_lengths = np.hstack((
                x_lengths[order[i:i + batch_size]],
                np.zeros((batch_size - size,)))
            )
            ans_type_batch = np.hstack((
                ans_type_labels[order[i:i + batch_size]],
                np.zeros((batch_size - size,)))
            )
            category_batch = np.hstack((
                category_labels[order[i:i + batch_size]],
                np.zeros((batch_size - size,)))
            )
            gender_batch = np.hstack((
                gender_labels[order[i:i + batch_size]],
                np.zeros((batch_size - size,)))
            )
            yield x_batch, y_batch, x_batch_lengths, ans_type_batch, category_batch, gender_batch
        else:
            break


def compute_lengths(x_data):
    return np.array([max(1, len(x)) for x in x_data])


def compute_ans_type_classes(properties: List[Dict]):
    i_to_class = ['abs', 'anim', 'char', 'event', 'org', 'people', 'place', 'work', 'missing']
    class_to_i = {key: index for index, key in enumerate(i_to_class)}
    for prop in properties:
        if prop['ans_type'] not in class_to_i:
            prop['ans_type'] = 'missing'
    return i_to_class, class_to_i


def compute_category_classes(properties: List[Dict]):
    i_to_class = ['Fine_Arts', 'History', 'Literature', 'Other', 'Science', 'Social_Science',
                  'missing']
    class_to_i = {key: index for index, key in enumerate(i_to_class)}
    for prop in properties:
        if prop['category'] not in class_to_i:
            prop['category'] = 'missing'
    return i_to_class, class_to_i


def compute_gender_classes(properties: List[Dict]):
    i_to_class = ['male', 'female', 'non_person', 'missing']
    class_to_i = {key: index for index, key in enumerate(i_to_class)}
    for prop in properties:
        if prop['gender'] not in class_to_i:
            prop['gender'] = 'missing'
    return i_to_class, class_to_i

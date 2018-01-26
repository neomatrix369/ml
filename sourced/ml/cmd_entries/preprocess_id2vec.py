import logging
import os
import shutil

import numpy
import tensorflow as tf

from modelforge.progress_bar import progress_bar
from sourced.ml.algorithms.id_embedding import _extract_coocc_matrix
from sourced.ml.models import Cooccurrences, DocumentFrequencies


def _int64s(xs):
    return tf.train.Feature(
        int64_list=tf.train.Int64List(value=list(xs)))


def _floats(xs):
    return tf.train.Feature(
        float_list=tf.train.FloatList(value=list(xs)))


def preprocess_id2vec(args):
    """
    Loads co-occurrence matrices for several repositories and generates the
    document frequencies and the Swivel protobuf dataset.

    :param args: :class:`argparse.Namespace` with "input", "vocabulary_size", \
                 "shard_size", "df" and "output".
    :return: None
    """
    log = logging.getLogger("preproc")
    df_model = DocumentFrequencies().load(source=args.docfreq)
    coocc_model = Cooccurrences().load(args.input)
    if coocc_model.meta['dependencies']:
        try:
            df_meta = coocc_model.get_dep("docfreq")
            if df_model.meta != df_meta:
                raise ValueError(("Document frequency model you provided does not match "
                                  "dependency inside Cooccurrences model:\n"
                                  "args.docfreq.meta:\n{}\n"
                                  "coocc_model.get_dep(\"docfreq\")\n{}\n").format(df_model.meta,
                                                                                   df_meta))
        except KeyError:
            pass  # There is no docfreq dependency

    all_words = df_model.docfreq
    vs = args.vocabulary_size
    if len(all_words) < vs:
        vs = len(all_words)
    sz = args.shard_size
    if vs < sz:
        raise ValueError(
            "vocabulary_size={0} is less than shard_size={1}. "
            "You should specify smaller shard_size "
            "(pass shard_size={0} argument).".format(vs, sz))
    vs -= vs % sz
    log.info("Effective vocabulary size: %d", vs)
    log.info("Truncating the vocabulary...")
    words = numpy.array(list(all_words.keys()))
    freqs = numpy.array(list(all_words.values()), dtype=numpy.int64)
    del all_words, df_model
    chosen_indices = numpy.argpartition(
        freqs, len(freqs) - vs)[len(freqs) - vs:]
    chosen_freqs = freqs[chosen_indices]
    chosen_words = words[chosen_indices]
    border_freq = chosen_freqs.min()
    border_mask = chosen_freqs == border_freq
    border_num = border_mask.sum()
    border_words = words[freqs == border_freq]
    border_words = numpy.sort(border_words)
    chosen_words[border_mask] = border_words[:border_num]
    del words
    del freqs
    log.info("Sorting the vocabulary...")
    sorted_indices = numpy.argsort(chosen_words)
    chosen_freqs = chosen_freqs[sorted_indices]
    chosen_words = chosen_words[sorted_indices]
    word_indices = {w: i for i, w in enumerate(chosen_words)}
    del chosen_freqs

    if not os.path.exists(args.output):
        os.makedirs(args.output)

    with open(os.path.join(args.output, "row_vocab.txt"), "w") as out:
        out.write('\n'.join(chosen_words))
    log.info("Saved row_vocab.txt...")
    shutil.copyfile(os.path.join(args.output, "row_vocab.txt"),
                    os.path.join(args.output, "col_vocab.txt"))
    log.info("Saved col_vocab.txt...")

    del chosen_words

    ccmatrix = _extract_coocc_matrix((vs, vs), word_indices, coocc_model)

    log.info("Planning the sharding...")
    bool_sums = ccmatrix.indptr[1:] - ccmatrix.indptr[:-1]
    with open(os.path.join(args.output, "row_sums.txt"), "w") as out:
        out.write('\n'.join(map(str, bool_sums.tolist())))
    log.info("Saved row_sums.txt...")
    shutil.copyfile(os.path.join(args.output, "row_sums.txt"),
                    os.path.join(args.output, "col_sums.txt"))
    log.info("Saved col_sums.txt...")
    reorder = numpy.argsort(-bool_sums)
    log.info("Writing the shards...")
    os.makedirs(args.output, exist_ok=True)
    nshards = vs // args.shard_size
    for row in progress_bar(range(nshards), log, expected_size=nshards):
        for col in range(nshards):
            indices_row = reorder[row::nshards]
            indices_col = reorder[col::nshards]
            shard = ccmatrix[indices_row][:, indices_col].tocoo()

            example = tf.train.Example(features=tf.train.Features(feature={
                "global_row": _int64s(indices_row),
                "global_col": _int64s(indices_col),
                "sparse_local_row": _int64s(shard.row),
                "sparse_local_col": _int64s(shard.col),
                "sparse_value": _floats(shard.data)}))

            with open(os.path.join(args.output,
                                   "shard-%03d-%03d.pb" % (row, col)),
                      "wb") as out:
                out.write(example.SerializeToString())
    log.info("Success")
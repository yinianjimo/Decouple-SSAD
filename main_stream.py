# -*- coding: utf-8 -*-
"""
@author: HYPJUDY 2019/4/15
https://github.com/HYPJUDY

Single Shot Temporal Action Detection
-----------------------------------------------------------------------------------

Train, test and post-processing for the main stream of Decouple-SSAD
Improved version of SSAD

Usage:
Please refer to `run.sh` for details.
e.g.
`python main_stream.py test UCF101 temporal main_stream main_stream`

"""

from operations import *
from load_data import get_train_data, get_test_data
from config import Config, get_models_dir, get_predict_result_path
import time
from os.path import join
import sys

####################################### PARAMETERS ########################################

stage = sys.argv[1]  # train/test/fuse/train_test_fuse
pretrain_dataset = sys.argv[2]  # UCF101/KnetV3
mode = sys.argv[3]  # temporal/spatial
method = sys.argv[4]
method_temporal = sys.argv[5]  # used for final result fusing

if (mode == 'spatial' and pretrain_dataset == 'Anet') or pretrain_dataset == 'KnetV3':
    feature_dim = 2048
else:
    feature_dim = 1024

models_dir = get_models_dir(mode, pretrain_dataset, method)
models_file_prefix = join(models_dir, 'model-ep')
test_checkpoint_file = join(models_dir, 'model-ep-30')
predict_file = get_predict_result_path(mode, pretrain_dataset, method)


######################################### TRAIN ##########################################

def train_operation(X, Y_label, Y_bbox, Index, LR, config):
    bsz = config.batch_size
    ncls = config.num_classes

    net = base_feature_network(X)
    MALs = main_anchor_layer(net)

    # --------------------------- Main Stream -----------------------------
    full_mainAnc_class = tf.reshape(tf.constant([]), [bsz, -1, ncls])
    full_mainAnc_conf = tf.reshape(tf.constant([]), [bsz, -1])
    full_mainAnc_xmin = tf.reshape(tf.constant([]), [bsz, -1])
    full_mainAnc_xmax = tf.reshape(tf.constant([]), [bsz, -1])

    full_mainAnc_BM_x = tf.reshape(tf.constant([]), [bsz, -1])
    full_mainAnc_BM_w = tf.reshape(tf.constant([]), [bsz, -1])
    full_mainAnc_BM_labels = tf.reshape(tf.constant([], dtype=tf.int32), [bsz, -1, ncls])
    full_mainAnc_BM_scores = tf.reshape(tf.constant([]), [bsz, -1])

    for i, ln in enumerate(config.layers_name):
        mainAnc = mulClsReg_predict_layer(config, MALs[i], ln, 'mainStream')

        # --------------------------- Main Stream -----------------------------
        [mainAnc_BM_x, mainAnc_BM_w, mainAnc_BM_labels, mainAnc_BM_scores,
         mainAnc_class, mainAnc_conf, mainAnc_rx, mainAnc_rw] = \
            anchor_bboxes_encode(mainAnc, Y_label, Y_bbox, Index, config, ln)

        mainAnc_xmin = mainAnc_rx - mainAnc_rw / 2
        mainAnc_xmax = mainAnc_rx + mainAnc_rw / 2

        full_mainAnc_class = tf.concat([full_mainAnc_class, mainAnc_class], axis=1)
        full_mainAnc_conf = tf.concat([full_mainAnc_conf, mainAnc_conf], axis=1)
        full_mainAnc_xmin = tf.concat([full_mainAnc_xmin, mainAnc_xmin], axis=1)
        full_mainAnc_xmax = tf.concat([full_mainAnc_xmax, mainAnc_xmax], axis=1)

        full_mainAnc_BM_x = tf.concat([full_mainAnc_BM_x, mainAnc_BM_x], axis=1)
        full_mainAnc_BM_w = tf.concat([full_mainAnc_BM_w, mainAnc_BM_w], axis=1)
        full_mainAnc_BM_labels = tf.concat([full_mainAnc_BM_labels, mainAnc_BM_labels], axis=1)
        full_mainAnc_BM_scores = tf.concat([full_mainAnc_BM_scores, mainAnc_BM_scores], axis=1)

    main_class_loss, main_loc_loss, main_conf_loss = \
        loss_function(full_mainAnc_class, full_mainAnc_conf,
                      full_mainAnc_xmin, full_mainAnc_xmax,
                      full_mainAnc_BM_x, full_mainAnc_BM_w,
                      full_mainAnc_BM_labels, full_mainAnc_BM_scores, config)

    loss = main_class_loss + config.p_loc * main_loc_loss + config.p_conf * main_conf_loss

    trainable_variables = get_trainable_variables()
    optimizer = tf.train.AdamOptimizer(learning_rate=LR).minimize(loss, var_list=trainable_variables)

    return optimizer, loss, trainable_variables


def train_main(config):
    bsz = config.batch_size

    tf.set_random_seed(config.seed)
    X = tf.placeholder(tf.float32, shape=(bsz, config.input_steps, feature_dim))
    Y_label = tf.placeholder(tf.int32, [None, config.num_classes])
    Y_bbox = tf.placeholder(tf.float32, [None, 3])
    Index = tf.placeholder(tf.int32, [bsz + 1])
    LR = tf.placeholder(tf.float32)

    optimizer, loss, trainable_variables = \
        train_operation(X, Y_label, Y_bbox, Index, LR, config)

    model_saver = tf.train.Saver(var_list=trainable_variables, max_to_keep=2)

    sess = tf.InteractiveSession(config=tf.ConfigProto(log_device_placement=False))

    tf.global_variables_initializer().run()

    # initialize parameters or restore from previous model
    if not os.path.exists(models_dir):
        os.makedirs(models_dir)
    if os.listdir(models_dir) == [] or config.initialize:
        init_epoch = 0
        print ("Initializing Network")
    else:
        init_epoch = int(config.steps)
        restore_checkpoint_file = join(models_dir, 'model-ep-' + str(config.steps - 1))
        model_saver.restore(sess, restore_checkpoint_file)

    batch_train_dataX, batch_train_gt_label, batch_train_gt_info, batch_train_index = \
        get_train_data(config, mode, pretrain_dataset, True)
    num_batch_train = len(batch_train_dataX)

    for epoch in range(init_epoch, config.training_epochs):

        loss_info = []

        for idx in range(num_batch_train):
            feed_dict = {X: batch_train_dataX[idx],
                         Y_label: batch_train_gt_label[idx],
                         Y_bbox: batch_train_gt_info[idx],
                         Index: batch_train_index[idx],
                         LR: config.learning_rates[epoch]}
            _, out_loss = sess.run([optimizer, loss], feed_dict=feed_dict)

            loss_info.append(out_loss)

        print ("Training epoch ", epoch, " loss: ", np.mean(loss_info))

        if epoch == config.training_epochs - 2 or epoch == config.training_epochs - 1:
            model_saver.save(sess, models_file_prefix, global_step=epoch)


########################################### TEST ############################################

def test_operation(X, config):
    bsz = config.batch_size
    ncls = config.num_classes

    net = base_feature_network(X)
    MALs = main_anchor_layer(net)

    full_mainAnc_class = tf.reshape(tf.constant([]), [bsz, -1, ncls])
    full_mainAnc_conf = tf.reshape(tf.constant([]), [bsz, -1])
    full_mainAnc_xmin = tf.reshape(tf.constant([]), [bsz, -1])
    full_mainAnc_xmax = tf.reshape(tf.constant([]), [bsz, -1])

    for i, ln in enumerate(config.layers_name):
        mainAnc = mulClsReg_predict_layer(config, MALs[i], ln, 'mainStream')

        mainAnc_class, mainAnc_conf, mainAnc_rx, mainAnc_rw = anchor_box_adjust(mainAnc, config, ln)

        mainAnc_xmin = mainAnc_rx - mainAnc_rw / 2
        mainAnc_xmax = mainAnc_rx + mainAnc_rw / 2

        full_mainAnc_class = tf.concat([full_mainAnc_class, mainAnc_class], axis=1)
        full_mainAnc_conf = tf.concat([full_mainAnc_conf, mainAnc_conf], axis=1)
        full_mainAnc_xmin = tf.concat([full_mainAnc_xmin, mainAnc_xmin], axis=1)
        full_mainAnc_xmax = tf.concat([full_mainAnc_xmax, mainAnc_xmax], axis=1)

    full_mainAnc_class = tf.nn.softmax(full_mainAnc_class, dim=-1)
    return full_mainAnc_class, full_mainAnc_conf, full_mainAnc_xmin, full_mainAnc_xmax


def test_main(config):
    batch_dataX, batch_winInfo = get_test_data(config, mode, pretrain_dataset)

    X = tf.placeholder(tf.float32, shape=(config.batch_size, config.input_steps, feature_dim))

    anchors_class, anchors_conf, anchors_xmin, anchors_xmax = test_operation(X, config)

    model_saver = tf.train.Saver()
    sess = tf.InteractiveSession(config=tf.ConfigProto(log_device_placement=False))
    tf.global_variables_initializer().run()
    model_saver.restore(sess, test_checkpoint_file)

    batch_result_class = []
    batch_result_conf = []
    batch_result_xmin = []
    batch_result_xmax = []

    num_batch = len(batch_dataX)
    for idx in range(num_batch):
        out_anchors_class, out_anchors_conf, out_anchors_xmin, out_anchors_xmax = \
            sess.run([anchors_class, anchors_conf, anchors_xmin, anchors_xmax],
                     feed_dict={X: batch_dataX[idx]})
        batch_result_class.append(out_anchors_class)
        batch_result_conf.append(out_anchors_conf)
        batch_result_xmin.append(out_anchors_xmin * config.window_size)
        batch_result_xmax.append(out_anchors_xmax * config.window_size)

    outDf = pd.DataFrame(columns=config.outdf_columns)

    for i in range(num_batch):
        tmpDf = result_process(batch_winInfo, batch_result_class, batch_result_conf,
                               batch_result_xmin, batch_result_xmax, config, i)

        outDf = pd.concat([outDf, tmpDf])
    if config.save_predict_result:
        outDf.to_csv(predict_file, index=False)
    return outDf


if __name__ == "__main__":
    config = Config()
    start_time = time.time()
    elapsed_time = 0
    if stage == 'train':
        train_main(config)
        elapsed_time = time.time() - start_time
    elif stage == 'test':
        df = test_main(config)
        elapsed_time = time.time() - start_time
        final_result_process(stage, pretrain_dataset, config, mode, method, '', df)
    elif stage == 'fuse':
        final_result_process(stage, pretrain_dataset, config, mode, method, method_temporal)
        elapsed_time = time.time() - start_time
    elif stage == 'train_test_fuse':
        train_main(config)
        elapsed_time = time.time() - start_time
        tf.reset_default_graph()
        df = test_main(config)
        final_result_process(stage, pretrain_dataset, config, mode, method, '', df)
    else:
        print ("No stage", stage, "Please choose a stage from train/test/fuse/train_test_fuse.")
    print ("Elapsed time:", elapsed_time, "start time:", start_time)

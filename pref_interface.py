#!/usr/bin/env python

import queue
import time
from itertools import combinations
from multiprocessing import Process, Queue

import numpy as np
import pyglet
from dot_utils import predict_action_preference
from numpy.testing import assert_equal
from reward_predictor import RewardPredictorEnsemble


class Im(object):
    def __init__(self, display=None):
        self.window = None
        self.isopen = False
        self.display = display

    def imshow(self, arr):
        if self.window is None:
            height, width = arr.shape
            self.window = pyglet.window.Window(
                width=width, height=height, display=self.display)
            self.width = width
            self.height = height
            self.isopen = True
        assert arr.shape == (
            self.height,
            self.width), "You passed in an image with the wrong number shape"
        image = pyglet.image.ImageData(
            self.width, self.height, 'L', arr.tobytes(), pitch=-self.width)
        self.window.clear()
        self.window.switch_to()
        self.window.dispatch_events()
        image.blit(0, 0)
        self.window.flip()

    def close(self):
        if self.isopen:
            self.window.close()
            self.isopen = False

    def __del__(self):
        self.close()


class PrefInterface:

    def __init__(self, headless):
        self.vid_q = Queue()
        if not headless:
            Process(target=vid_proc, args=(self.vid_q,), daemon=True).start()

    def get_seg_pair(self, segments, pair_idxs):
        """
        - Calculate predicted preferences for every possible pair of segments
        - Calculate the pair with the highest uncertainty
        - Return the index of that pair of segments
        - Send that pair of segments, along with segment IDs, to be checked by
          the user
        """
        s1s = []
        s2s = []
        for i1, i2 in pair_idxs:
            s1s.append(segments[i1])
            s2s.append(segments[i2])
        pair_preds = self.reward_model.preferences(s1s, s2s)
        pair_preds = np.array(pair_preds)
        n_preds = self.reward_model.n_preds
        assert_equal(pair_preds.shape, (n_preds, len(pair_idxs), 2))

        # Each predictor gives two outputs:
        # - p1: the probability of segment 1 being preferred
        # - p2: the probability of segment 2 being preferred
        #       (= 1 - p1)
        # We want to calculate variance of predictions across all
        # predictors in the ensemble.
        # If L is a list, var(L) = var(1 - L).
        # So we can calculate the variance based on either p1 or p2
        # and get the same result.
        preds = pair_preds[:, :, 0]
        assert_equal(preds.shape, (n_preds, len(pair_idxs)))

        # Calculate variances across ensemble members
        pred_vars = np.var(preds, axis=0)
        assert_equal(pred_vars.shape, (len(pair_idxs), ))

        highest_var_i = np.argmax(pred_vars)
        check_idxs = pair_idxs[highest_var_i]
        check_s1 = segments[check_idxs[0]]
        check_s2 = segments[check_idxs[1]]

        return check_idxs, check_s1, check_s2

    def recv_segments(self, segments, seg_pipe, segs_max):
        n_segs = 0
        while True:
            try:
                segment = seg_pipe.get(timeout=0.1)
                n_segs += 1
            except queue.Empty:
                break
            segments.append(segment)
            # (The maximum number of segments kept being 5,000 isn't mentioned
            # in the paper anywhere - it's just something I decided on. This
            # should be maximum ~ 700 MB.)
            if len(segments) > segs_max:
                del segments[0]

    def sample_segments(self, segments):
        if len(segments) <= 10:
            idxs = range(len(segments))
        else:
            n_segs = len(segments)
            idxs = np.random.choice(range(n_segs),
                                    size=10, replace=False)
        return idxs

    def run(self, seg_pipe, pref_pipe, segs_max):
        tested_idxs = set()
        segments = []
        self.reward_model = RewardPredictorEnsemble('pref_interface')

        while True:
            self.recv_segments(segments, seg_pipe, segs_max)
            if len(segments) >= 2:
                break
            print("Not enough segments yet; sleeping...")
            time.sleep(1.0)

        while True:
            pair_idxs = []
            while len(pair_idxs) == 0:
                self.recv_segments(segments, seg_pipe, segs_max)
                idxs = self.sample_segments(segments)
                pair_idxs = set(combinations(idxs, 2))
                pair_idxs = pair_idxs - tested_idxs
                pair_idxs = list(pair_idxs)
            (n1, n2), s1, s2 = self.get_seg_pair(segments, pair_idxs)

            pref = predict_action_preference(s1, s2)
            pref_pipe.put((s1, s2, pref))
            tested_idxs.add((n1, n2))


def vid_proc(q):
    v = Im()
    segment = q.get(block=True, timeout=None)
    t = 0
    while True:
        v.imshow(segment[t])
        try:
            segment = q.get(block=False)
            if segment == "Pause":
                segment = q.get(block=True)
            t = 0
        except queue.Empty:
            t = (t + 1) % len(segment)
            time.sleep(1/15)

#!/usr/bin/env python
"""Learning function module for the mutation level correction.

Author: Federico Tomasi
Copyright (c) 2016, Federico Tomasi.
Licensed under the FreeBSD license (see LICENSE.txt).
"""
from __future__ import division, print_function

import logging
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import os
import seaborn as sns
import six
import warnings

from functools import partial
from itertools import chain
# from scipy.optimize import curve_fit
from scipy.optimize import least_squares
from sklearn import mixture

from icing.core import cloning
from icing.core import parallel_distance
from icing.models.model import model_matrix
from icing.utils import io, extra


def least_squares_mdl(x, u):
    """Model for least squares. Used by scipy.optimize.least_squares."""
    return x[0] * (u ** 2 + x[1] * u) / (u ** 2 + x[2] * u + x[3])


def least_squares_jacobian(x, u, y):
    """Jacobian for least squares. Used by scipy.optimize.least_squares."""
    J = np.empty((u.size, x.size))
    den = u ** 2 + x[2] * u + x[3]
    num = u ** 2 + x[1] * u
    J[:, 0] = num / den
    J[:, 1] = x[0] * u / den
    J[:, 2] = -x[0] * num * u / den ** 2
    J[:, 3] = -x[0] * num / den ** 2
    return J


def remove_duplicate_junctions(igs_list):
    """Remove igs which have same junction."""
    igs, juncs = [], []
    for ig in igs_list:
        junc = extra.junction_re(ig.junction)
        if junc not in juncs:
            igs.append(ig)
            juncs.append(junc)
    return igs, juncs

# def remove_duplicate_junctions(igs):
#     igs = list(igs)
#     return igs, map(lambda x: extra.junction_re(x.junction), igs)


def make_hist(juncs1, juncs2, filename, lim_mut1, lim_mut2, type_ig='Mem',
              mut=None, donor1='B4', donor2=None, bins=100, max_seqs=1000,
              min_seqs=0, ig1=None, ig2=None, is_intra=True,
              sim_func_args=None):
    """Make histogram and main computation of nearest similarities."""
    if os.path.exists(filename + '.npz'):
        logging.critical(filename + '.npz esists.')
        return filename
    if len(juncs1) < min_seqs or len(juncs2) < min_seqs:
        return ''

    # sample if length is exceeded (for computational costs)
    from sklearn.utils import shuffle
    if len(juncs1) > max_seqs:
        ig1, juncs1 = shuffle(ig1, juncs1)
        ig1 = ig1[:max_seqs]
        juncs1 = juncs1[:max_seqs]
    if len(juncs2) > max_seqs:
        ig2, juncs2 = shuffle(ig2, juncs2)
        ig2 = ig2[:max_seqs]
        juncs2 = juncs2[:max_seqs]

    sim_func_args['correct'] = False
    sim_func_args['tol'] = 1000
    default_model = 'ham'
    sim_func_args.setdefault('model', default_model)
    sim_func_args.setdefault('dist_mat', model_matrix(default_model))
    sim_func_args.setdefault(
        'ssk_params', {'min_kn': 1, 'max_kn': 8, 'lamda': .75})

    dd = cloning.inverse_index(ig1 if is_intra else ig1 + ig2)
    if sim_func_args.setdefault('method', 'jaccard') \
            in ('pcc', 'hypergeometric'):
        sim_func_args['sim_score_params'] = {
            'nV': len([x for x in dd if 'V' in x]),
            'nJ': len([x for x in dd if 'J' in x])
        }
    sim_func = partial(cloning.sim_function, **sim_func_args)
    logging.info("Computing %s", filename)
    if is_intra:
        # dnearest = parallel_distance.dnearest_inter_padding(
        #     ig1, ig1, sim_func, filt=lambda x: 0 < x, func=max)
        dnearest = parallel_distance.dnearest_intra_padding(
            ig1, sim_func, filt=lambda x: x > 0, func=max)
        # ig1, ig1, sim_func, filt=lambda x: 0 < x < 1, func=max)
    else:
        dnearest = parallel_distance.dnearest_inter_padding(
            ig1, ig2, sim_func, filt=lambda x: 0 < x < 1, func=max)
    if not os.path.exists(filename.split('/')[0]):
        os.makedirs(filename.split('/')[0])
    np.savez(filename, X=dnearest, mut=mut)

    # Plot distance distribution
    plt.figure(figsize=(20, 10))
    plt.hist(dnearest, bins=bins, normed=True)
    plt.title("Distances between " +
              ("{}-{}".format(donor1, donor2) if donor2 else "") +
              " {} {:.3f}-{:.3f}% and {:.3f}-{:.3f}%"
              .format(type_ig, lim_mut1[0], lim_mut1[1], *lim_mut2))
    plt.ylabel('Count')
    plt.xlim([0, 1])
    plt.xticks(np.linspace(0, 1, 21))
    plt.xlabel('Ham distance (normalised)')
    plt.savefig(filename + ".png")
    plt.close()
    del dnearest
    return filename


def intra_donor_distance(f='', lim_mut1=(0, 0), lim_mut2=(0, 0), type_ig='Mem',
                         quantity=.15, donor='B4', bins=100, max_seqs=1000,
                         min_seqs=100, sim_func_args=None):
    """Nearest distances intra donor.

    Subsets of Igs can be selected choosing two ranges of mutations.
    """
    filename = \
        "{0}/dist2nearest_{0}_{1}-{2}_vs_{3}-{4}_{5}bins_norm_{6}maxseqs" \
        .format(donor, lim_mut1[0], lim_mut1[1], lim_mut2[0],
                lim_mut2[1], bins, max_seqs)
    # mut = min(lim_mut1[0], lim_mut2[0])
    if os.path.exists(filename + '.npz'):
        logging.info("File %s exists.", filename + '.npz')
        return filename, float(np.load(filename + '.npz')['mut'])

    n_tot = io.get_num_records(f)
    if max(lim_mut1[1], lim_mut2[1]) == 0:
        igs = io.read_db(f, filt=(lambda x: x.mut == 0),
                         max_records=quantity * n_tot)
        igs1, juncs1 = remove_duplicate_junctions(igs)
        juncs2 = juncs1
        igs2 = igs1
        mut = 0
    else:
        igs = io.read_db(f,
                         filt=(lambda x: lim_mut1[0] < x.mut <= lim_mut1[1]),
                         max_records=quantity * n_tot)
        igs1, juncs1 = remove_duplicate_junctions(igs)
        igs = io.read_db(f,
                         filt=(lambda x: lim_mut2[0] < x.mut <= lim_mut2[1]),
                         max_records=quantity * n_tot)
        igs2, juncs2 = remove_duplicate_junctions(igs)
        if not len(juncs1) or not len(juncs2):
            return '', 0
        mut = np.mean(list(chain((x.mut for x in igs1),
                                 (x.mut for x in igs2))))
    return make_hist(
        juncs1, juncs2, filename, lim_mut1, lim_mut2, type_ig, mut,
        donor, None, bins, max_seqs, min_seqs, ig1=igs1, ig2=igs2,
        sim_func_args=sim_func_args), mut


def inter_donor_distance(f1='', f2='', lim_mut1=(0, 0), lim_mut2=(0, 0),
                         type_ig='Mem', donor1='B4', donor2='B5', bins=100,
                         max_seqs=1000, quantity=.15, sim_func_args=None):
    """Nearest distances inter donors.

    Igs involved can be selected by choosing two possibly different ranges
    of mutations.
    """
    filename = \
        "{0}/dnearest_{0}_{1}_{2}-{3}_vs_{4}-{5}_{6}bins_norm_{7}maxseqs" \
        .format(donor1, donor2, lim_mut1[0], lim_mut1[1],
                lim_mut2[0], lim_mut2[1], bins, max_seqs)
    # mut = min(lim_mut1[0], lim_mut2[0])
    if os.path.exists(filename + '.npz'):
        logging.info("File %s exists.", filename + '.npz')
        return filename, float(np.load(filename + '.npz')['mut'])

    if max(lim_mut1[1], lim_mut2[1]) == 0:
        igs = io.read_db(f1, filt=(lambda x: x.mut == 0))
        _, juncs1 = remove_duplicate_junctions(igs)
        igs = io.read_db(f2, filt=(lambda x: x.mut == 0))
        _, juncs2 = remove_duplicate_junctions(igs)
        mut = 0
    elif max(lim_mut1[1], lim_mut2[1]) < 0:
        # not specified: get at random
        igs = io.read_db(f1)
        _, juncs1 = remove_duplicate_junctions(igs)
        igs = io.read_db(f2)
        _, juncs2 = remove_duplicate_junctions(igs)
    else:
        igs = io.read_db(
            f1, filt=(lambda x: lim_mut1[0] < x.mut <= lim_mut1[1]))
        _, juncs1 = remove_duplicate_junctions(igs)
        igs = io.read_db(
            f2, filt=(lambda x: lim_mut2[0] < x.mut <= lim_mut2[1]))
        _, juncs2 = remove_duplicate_junctions(igs)

    juncs1 = juncs1[:int(quantity * len(juncs1))]
    juncs2 = juncs2[:int(quantity * len(juncs2))]
    return make_hist(
        juncs1, juncs2, filename, lim_mut1, lim_mut2, type_ig, donor1,
        donor2, bins, max_seqs, sim_func_args=sim_func_args), mut


def distr_muts(db, quantity=0.15, bins=50, max_seqs=4000, min_seqs=100,
               sim_func_args=None):
    """Create histograms and relative mutation levels using intra groups."""
    logging.info("Analysing %s ...", db)
    try:
        max_mut = io.get_max_mut(db)
        # if max_mut < 1:
        n_tot = io.get_num_records(db)
        lin = np.linspace(0, max_mut, n_tot / 10.)
        sets = [(0, 0)] + zip(lin[:-1], lin[1:])
        # sets = [(0, 0)] + [(i - 1, i) for i in range(1, int(max_mut) + 1)]
        if len(sets) == 1:
            # no correction needs to be applied
            return None
        # sets = [(0, 0)] + zip(np.arange(0, max_mut, step),
        #                       np.arange(step, max_mut + step, step))
        out_muts = [
            intra_donor_distance(
                db, i, j, quantity=quantity, donor=db.split('/')[-1],
                bins=bins, max_seqs=max_seqs, min_seqs=min_seqs,
                sim_func_args=sim_func_args) for i, j in zip(sets, sets)]
    except StandardError as msg:
        logging.critical(msg)
        out_muts = []

    d = dict()
    for f, m in out_muts:
        d.setdefault(m, []).append(f)
    return d


def _gaussian_fit(array):
    if array.shape[0] < 2:
        logging.error("Cannot fit a Gaussian with two distances.")
        return 0

    array_2 = -(np.array(sorted(array)).reshape(-1, 1))
    array = np.array(list(array_2) + list(array)).reshape(-1, 1)

    try:
        # new sklearn GMM
        gmm = mixture.GaussianMixture(n_components=3,
                                      covariance_type='diag')
        gmm.fit(array)
        # gmmmean = np.max(gmm.means_)
        # gmmsigma = gmm.covariances_[np.argmax(gmm.means_)]
    except AttributeError:
        # use old sklearn method
        gmm = mixture.GMM(n_components=3)
        gmm.fit(array)
        # gmmmean = np.max(gmm.means_)
        # gmmsigma = gmm.covars_[np.argmax(gmm.means_)]

    # Extract optimal threshold
    plt.hist(array, bins=50, normed=True)  # debug, print

    lin = np.linspace(0, 1, 10000)[:, np.newaxis]
    # plt.plot(lin, np.exp(gmm.score_samples(lin)[0]), 'r')
    pred = gmm.predict(lin)
    try:
        idx = np.min(np.where(pred == np.argmax(gmm.means_))[0])
    except ValueError:
        # print("Error", np.unique(pred))
        idx = 0

    plt.axvline(x=lin[idx], linestyle='--', color='r')
    # plt.gcf().savefig("threshold_naive{}.png".format(k))
    plt.close()
    threshold = lin[idx][0]  # threshold
    # np.save("threshold_naive", threshold)
    return threshold


def learning_function(my_dict, order=3, alpha_plot='alphaplot.pdf'):
    """Learn the correction function given data in a dictionary.

    Parameters
    ----------
    mydict : dict
        Organised as {mut: [mean_similarities]}. Calculated by `distr_muts`.
    order : int, optional, default: 3
        Order of the learning function (polynomial).
    alpha_plot : str, optional, default: 'alpha_plot.pdf'
        Filename where to save the correction function plot.

    Returns
    -------
    func : function
        Function which accept the mutation level as parameter, returns the
        correction to apply on the similarity measure calculated.
    threshold : float
        Deprecated. Returns the threshold for methods like Hierarchical
        clustering to work in defining clones.
    """
    if my_dict is None:
        return lambda _: 1, 0
    d_dict = dict()
    samples, thresholds = [], []
    for k, v in six.iteritems(my_dict):
        for o in (_ for _ in v if _):
            dnearest = np.array(np.load("{}.npz".format(o))['X']) \
                .reshape(-1, 1)
            mean = np.mean(dnearest)
            samples.append(dnearest.shape[0])
            d_dict.setdefault(o.split('/')[0], dict()).setdefault(k, mean)

            # for the threshold, fit a gaussian (unused for AP)
            thresholds.append(_gaussian_fit(dnearest))

    # print(d_dict)
    for k, v in d_dict.iteritems():  # there is only one
        xvals = np.array(sorted([x for x in v]))
        yvals = np.array([np.mean(v[x]) for x in xvals])
        errors = np.array([np.var(v[x]) for x in xvals])

    # Take only significant values, higher than 0
    mask = yvals > 0
    xvals = xvals[mask]
    if xvals.shape[0] < 2:
        # no correction can be applied
        return lambda _: 1, 0

    yvals = yvals[mask]
    yvals = np.min(yvals) / yvals  # normalise
    errors = errors[mask]

    res = least_squares(
        lambda x, u, y: least_squares_mdl(x, u) - y,
        x0=np.array([2.5, 3.9, 4.15, 3.9]),
        jac=least_squares_jacobian, bounds=(0, 100), args=(xvals, yvals),
        ftol=1e-12, loss='soft_l1')

    with warnings.catch_warnings():
        warnings.filterwarnings('error')
        try:
            poly = np.poly1d(np.polyfit(xvals, yvals, order))
        except np.RankWarning:
            order = 2
            poly = np.poly1d(np.polyfit(xvals, yvals, order))

    with sns.axes_style('whitegrid'):
        sns.set_context('poster')
        xp = np.linspace(np.min(xvals), np.max(xvals), 1000)[:, None]
        plt.figure()
        plt.errorbar(xvals, yvals, errors, label='data', marker='s')
        plt.plot(xp, poly(xp), '-', label='order ' + str(order))
        # plt.plot(xp, fff(xp), '-', label='interpolate')
        plt.plot(xp, least_squares_mdl(res.x, xp), '-', label='least squares')
        plt.xlabel(r'Igs mutation level')
        plt.legend(loc='lower left')
        plt.savefig(alpha_plot, transparent=True)
        plt.close()

    # poly = partial(model, res.x)
    return poly, (filter(
        lambda x: x > 0,
        np.array(thresholds)[np.array(samples).argsort()[::-1]]) or [0])[0]


def generate_correction_function(db, quantity, sim_func_args=None, order=3,
                                 root=''):
    """Generate correction function on the database analysed."""
    db_no_ext = ".".join(db.split(".")[:-1])
    filename = db_no_ext + "_correction_function.npy"

    # case 1: file exists
    aplot = os.path.join(root, db_no_ext.split('/')[-1] + '_alphaplot.pdf')
    if os.path.exists(filename) and os.path.exists("threshold_naive.npy"):
        logging.critical("Best parameters exists. Loading them ...")
        popt = np.load(filename)
        threshold_naive = np.load("threshold_naive.npy")

    # case 2: file not exists
    else:
        my_dict = distr_muts(
            db, quantity=quantity, min_seqs=2, sim_func_args=sim_func_args)
        popt, threshold_naive = learning_function(my_dict, order, aplot)
        # save for later, in case of analysis on the same db
        # np.save(filename, popt)  # TODO

    # partial(extra.negative_exponential, a=popt[0], c=popt[1], d=popt[2]),
    return (popt, threshold_naive, aplot)

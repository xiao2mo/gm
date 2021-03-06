import itertools
import unittest

from numpy.testing import *
import numpy as np
import scipy as sp
import scipy.stats

from test_gmm import _generate_random_spd_matrix

import hmm

# import logging
# logging.basicConfig(level=logging.INFO,
#                     datefmt="%Y-%m-%d %H:%M:%S",
#                     format="%(asctime)s:%(levelname)s:%(message)s")

np.random.seed(0)


class TestHMM(unittest.TestCase):
    def test_hmm(self):
        h = hmm.HMM()

    def test_gaussian_hmm(self):
        h = hmm.HMM('gaussian')
        self.assertEquals(h.emission_type, 'gaussian')
        self.assertTrue(h.__class__, hmm.GaussianHMM)

    def test_gmm_hmm(self):
        h = hmm.HMM('gmm')
        self.assertEquals(h.emission_type, 'gmm')
        self.assertTrue(h.__class__, hmm.GMMHMM)


class TestBaseHMM(unittest.TestCase):
    class StubHMM(hmm._BaseHMM):
        emission_type = None
        def _compute_log_likelihood(self):
            pass
        def _generate_sample_from_state(self):
            pass
        def _init(self):
            pass
    
    def test_prune_states_no_pruning(self):
        h = self.StubHMM(10)
        lattice_frame = np.arange(h.nstates)

        idx = h._prune_states(lattice_frame, None, -np.Inf)
        assert_array_equal(idx, range(h.nstates))

    def test_prune_states_rank(self):
        h = self.StubHMM(10)
        lattice_frame = np.arange(h.nstates)

        idx = h._prune_states(lattice_frame, 1, -np.Inf)
        assert_array_equal(idx, [lattice_frame.argmax()])

    def test_prune_states_beam(self):
        h = self.StubHMM(10)
        lattice_frame = np.arange(h.nstates)

        beamlogprob = -h.nstates / 2
        idx = h._prune_states(lattice_frame, None, beamlogprob)
        refidx, = np.nonzero(lattice_frame >= -beamlogprob)
        assert_array_equal(idx, refidx)

    def setup_example_hmm(self):
        # Example from http://en.wikipedia.org/wiki/Forward-backward_algorithm
        h = self.StubHMM(2)
        h.transmat = [[0.7, 0.3], [0.3, 0.7]]
        h.start_prob = [0.5, 0.5]
        framelogprob = np.log([[0.9, 0.2],
                               [0.9, 0.2],
                               [0.1, 0.8],
                               [0.9, 0.2],
                               [0.9, 0.2]])
        # Add dummy observations to stub.
        h._compute_log_likelihood = lambda obs: framelogprob
        return h, framelogprob

    def test_init(self):
        h, framelogprob = self.setup_example_hmm()

        for params in [('transmat',), ('startprob', 'transmat')]:
            d = dict((x, getattr(h, x)) for x in params)
            h2 = self.StubHMM(h.nstates, **d)
            self.assertEqual(h.nstates, h2.nstates)
            for p in params:
                assert_array_almost_equal(getattr(h, p), getattr(h2, p))

    def test_do_forward_pass(self):
        h, framelogprob = self.setup_example_hmm()

        logprob, fwdlattice = h._do_forward_pass(framelogprob)

        reflogprob = -3.3725
        self.assertAlmostEqual(logprob, reflogprob, places=4)

        reffwdlattice = np.array([[0.4500, 0.1000],
                                  [0.3105, 0.0410],
                                  [0.0230, 0.0975],
                                  [0.0408, 0.0150],
                                  [0.0298, 0.0046]])
        assert_array_almost_equal(np.exp(fwdlattice), reffwdlattice, 4)

    def test_do_backward_pass(self):
        h, framelogprob = self.setup_example_hmm()

        fakefwdlattice = np.zeros((len(framelogprob), 2))
        bwdlattice = h._do_backward_pass(framelogprob, fakefwdlattice)
        
        refbwdlattice = np.array([[0.0661, 0.0455],
                                  [0.0906, 0.1503],
                                  [0.4593, 0.2437],
                                  [0.6900, 0.4100],
                                  [1.0000, 1.0000]])
        assert_array_almost_equal(np.exp(bwdlattice), refbwdlattice, 4)

    def test_do_viterbi_pass(self):
        h, framelogprob = self.setup_example_hmm()

        logprob, state_sequence = h._do_viterbi_pass(framelogprob)

        refstate_sequence = [0, 0, 1, 0, 0]
        assert_array_equal(state_sequence, refstate_sequence)

        reflogprob = -4.3500
        self.assertAlmostEqual(logprob, reflogprob, places=4)

    def test_eval(self):
        h, framelogprob = self.setup_example_hmm()
        nobs = len(framelogprob)

        logprob, posteriors = h.eval([])

        assert_array_almost_equal(posteriors.sum(axis=1), np.ones(nobs))

        reflogprob = -3.3725
        self.assertAlmostEqual(logprob, reflogprob, places=4)

        refposteriors = np.array([[0.8673, 0.1327],
                                  [0.8204, 0.1796],
                                  [0.3075, 0.6925],
                                  [0.8204, 0.1796],
                                  [0.8673, 0.1327]])
        assert_array_almost_equal(posteriors, refposteriors, decimal=4)

    def test_hmm_eval_consistent_with_gmm(self):
        nstates = 8
        nobs = 10
        h = self.StubHMM(nstates)

        # Add dummy observations to stub.
        framelogprob = np.log(np.random.rand(nobs, nstates))
        h._compute_log_likelihood = lambda obs: framelogprob

        # If startprob and transmat are uniform across all states (the
        # default), the transitions are uninformative - the model
        # reduces to a GMM with uniform mixing weights (in terms of
        # posteriors, not likelihoods).
        logprob, hmmposteriors = h.eval([])

        assert_array_almost_equal(hmmposteriors.sum(axis=1), np.ones(nobs))

        norm = hmm.logsum(framelogprob, axis=1)[:,np.newaxis]
        gmmposteriors = np.exp(framelogprob - np.tile(norm,  (1, nstates)))
        assert_array_almost_equal(hmmposteriors, gmmposteriors)

    def test_hmm_decode_consistent_with_gmm(self):
        nstates = 8
        nobs = 10
        h = self.StubHMM(nstates)

        # Add dummy observations to stub.
        framelogprob = np.log(np.random.rand(nobs, nstates))
        h._compute_log_likelihood = lambda obs: framelogprob

        # If startprob and transmat are uniform across all states (the
        # default), the transitions are uninformative - the model
        # reduces to a GMM with uniform mixing weights (in terms of
        # posteriors, not likelihoods).
        viterbi_ll, state_sequence = h.decode([])

        norm = hmm.logsum(framelogprob, axis=1)[:,np.newaxis]
        gmmposteriors = np.exp(framelogprob - np.tile(norm,  (1, nstates)))
        gmmstate_sequence = gmmposteriors.argmax(axis=1)
        assert_array_equal(state_sequence, gmmstate_sequence)

    def test_base_hmm_attributes(self):
        nstates = 20
        startprob = np.random.rand(nstates)
        startprob = startprob / startprob.sum()
        transmat = np.random.rand(nstates, nstates)
        transmat /= np.tile(transmat.sum(axis=1)[:,np.newaxis], (1, nstates))

        h = self.StubHMM(nstates)
        
        self.assertEquals(h.nstates, nstates)

        h.startprob = startprob
        assert_array_almost_equal(h.startprob, startprob)

        self.assertRaises(ValueError, h.__setattr__, 'startprob',
                          2 * startprob)
        self.assertRaises(ValueError, h.__setattr__, 'startprob', [])
        self.assertRaises(ValueError, h.__setattr__, 'startprob',
                          np.zeros((nstates - 2, 2)))

        h.transmat = transmat
        assert_array_almost_equal(h.transmat, transmat)
        self.assertRaises(ValueError, h.__setattr__, 'transmat',
                          2 * transmat)
        self.assertRaises(ValueError, h.__setattr__, 'transmat', [])
        self.assertRaises(ValueError, h.__setattr__, 'transmat',
                          np.zeros((nstates - 2, nstates)))


class GaussianHMMParams(object):
    nstates = 5
    ndim = 3
    startprob = np.random.rand(nstates)
    startprob = startprob / startprob.sum()
    transmat = np.random.rand(nstates, nstates)
    transmat /= np.tile(transmat.sum(axis=1)[:,np.newaxis], (1, nstates))
    means = np.random.randint(-20, 20, (nstates, ndim))
    covars = {'spherical': (1.0 + 2 * np.random.rand(nstates))**2,
              'tied': _generate_random_spd_matrix(ndim) + np.eye(ndim),
              'diag': (1.0 + 2 * np.random.rand(nstates, ndim))**2,
              'full': np.array([_generate_random_spd_matrix(ndim) + np.eye(ndim)
                                for x in xrange(nstates)])}


class GaussianHMMTester(GaussianHMMParams):
    def test_bad_cvtype(self):
        h = hmm.GaussianHMM(20, 1, self.cvtype)
        self.assertRaises(ValueError, hmm.HMM, 20, 1, 'badcvtype')

    def test_attributes(self):
        h = hmm.GaussianHMM(self.nstates, self.ndim, self.cvtype)

        self.assertEquals(h.emission_type, 'gaussian')
        
        self.assertEquals(h.nstates, self.nstates)
        self.assertEquals(h.ndim, self.ndim)
        self.assertEquals(h.cvtype, self.cvtype)

        h.startprob = self.startprob
        assert_array_almost_equal(h.startprob, self.startprob)
        self.assertRaises(ValueError, h.__setattr__, 'startprob',
                          2 * self.startprob)
        self.assertRaises(ValueError, h.__setattr__, 'startprob', [])
        self.assertRaises(ValueError, h.__setattr__, 'startprob',
                          np.zeros((self.nstates - 2, self.ndim)))

        h.transmat = self.transmat
        assert_array_almost_equal(h.transmat, self.transmat)
        self.assertRaises(ValueError, h.__setattr__, 'transmat',
                          2 * self.transmat)
        self.assertRaises(ValueError, h.__setattr__, 'transmat', [])
        self.assertRaises(ValueError, h.__setattr__, 'transmat',
                          np.zeros((self.nstates - 2, self.nstates)))

        h.means = self.means
        assert_array_almost_equal(h.means, self.means)
        self.assertRaises(ValueError, h.__setattr__, 'means', [])
        self.assertRaises(ValueError, h.__setattr__, 'means',
                          np.zeros((self.nstates - 2, self.ndim)))

        h.covars = self.covars[self.cvtype]
        assert_array_almost_equal(h.covars, self.covars[self.cvtype])
        self.assertRaises(ValueError, h.__setattr__, 'covars', [])
        self.assertRaises(ValueError, h.__setattr__, 'covars',
                          np.zeros((self.nstates - 2, self.ndim)))

    def test_eval_and_decode(self):
        h = hmm.GaussianHMM(self.nstates, self.ndim, self.cvtype,
                            means=self.means, covars=self.covars[self.cvtype])
        # Make sure the means are far apart so posteriors.argmax()
        # picks the actual component used to generate the observations.
        h.means = 20 * h.means

        gaussidx = np.repeat(range(self.nstates), 5)
        nobs = len(gaussidx)
        obs = np.random.randn(nobs, self.ndim) + h.means[gaussidx]

        ll, posteriors = h.eval(obs)

        self.assertEqual(posteriors.shape, (nobs, self.nstates))
        assert_array_almost_equal(posteriors.sum(axis=1), np.ones(nobs))

        viterbi_ll, stateseq = h.decode(obs)
        assert_array_equal(stateseq, gaussidx)

    def test_rvs(self, n=1000):
        h = hmm.GaussianHMM(self.nstates, self.ndim, self.cvtype)
        # Make sure the means are far apart so posteriors.argmax()
        # picks the actual component used to generate the observations.
        h.means = 20 * self.means
        h.covars = np.maximum(self.covars[self.cvtype], 0.1)
        h.startprob = self.startprob

        samples = h.rvs(n)
        self.assertEquals(samples.shape, (n, self.ndim))

    def test_train(self, params='stmc', niter=5):
        h = hmm.GaussianHMM(self.nstates, self.ndim, self.cvtype)
        h.startprob = self.startprob
        tmp = self.transmat + np.diag(np.random.rand(self.nstates))
        h.transmat = tmp / np.tile(tmp.sum(axis=1), (self.nstates, 1)).T
        h.means = 20 * self.means
        h.covars = self.covars[self.cvtype]

        # Create a training and testing set by sampling from the same
        # distribution.
        train_obs = [h.rvs(n=10) for x in xrange(50)]
        test_obs = [h.rvs(n=10) for x in xrange(5)]

        h.init(train_obs, minit='points')
        init_testll = [h.lpdf(x) for x in test_obs]

        trainll = h.train(train_obs, iter=niter, params=params)
        if not np.all(np.diff(trainll) > 0):
            print
            print 'Test train: %s (%s)\n  %s\n  %s' % (self.cvtype, params,
                                                       trainll, np.diff(trainll))
        self.assertTrue(np.all(np.diff(trainll) > -0.5))

        post_testll = [h.lpdf(x) for x in test_obs]
        if not (np.sum(post_testll) > np.sum(init_testll)):
            print
            print 'Test train: %s (%s)\n  %s\n  %s' % (self.cvtype, params,
                                                       init_testll, post_testll)
        self.assertTrue(np.sum(post_testll) > np.sum(init_testll))

    def test_train_covars(self):
        self.test_train('c')


class TestGaussianHMMWithSphericalCovars(unittest.TestCase, GaussianHMMTester):
    cvtype = 'spherical'

    def test_train_startprob_and_transmat(self):
        self.test_train('st')

    def test_train_means(self):
        self.test_train('m')


class TestGaussianHMMWithDiagonalCovars(unittest.TestCase, GaussianHMMTester):
    cvtype = 'diag'


class TestGaussianHMMWithTiedCovars(unittest.TestCase, GaussianHMMTester):
    cvtype = 'tied'


class TestGaussianHMMWithFullCovars(unittest.TestCase, GaussianHMMTester):
    cvtype = 'full'


class GaussianHMMMAPTrainerTester(GaussianHMMParams):
    def test_train(self, params='stmc', niter=5):
        covars_weight = 2.0
        if self.cvtype in ('full', 'tied'):
            covars_weight += self.ndim
        trainer = hmm.hmm_trainers.GaussianHMMMAPTrainer(
            startprob_prior=10*self.startprob + 2.0,
            transmat_prior=10*self.transmat + 2.0,
            means_prior=self.means,
            means_weight=2.0,
            covars_prior=self.covars[self.cvtype],
            covars_weight=covars_weight)
        h = hmm.GaussianHMM(self.nstates, self.ndim, self.cvtype,
                            trainer=trainer)
        h.startprob = self.startprob
        tmp = self.transmat + np.diag(np.random.rand(self.nstates))
        h.transmat = tmp / np.tile(tmp.sum(axis=1), (self.nstates, 1)).T
        h.means = 20 * self.means
        h.covars = self.covars[self.cvtype]

        # Create a training and testing set by sampling from the same
        # distribution.
        train_obs = [h.rvs(n=10) for x in xrange(10)]
        test_obs = [h.rvs(n=10) for x in xrange(5)]

        h.init(train_obs, minit='points')
        init_testll = [h.lpdf(x) for x in test_obs]

        trainll = h.train(train_obs, iter=niter, params=params)
        if not np.all(np.diff(trainll) > 0):
            print
            print 'Test MAP train: %s (%s)\n  %s\n  %s' % (self.cvtype, params,
                                                       trainll, np.diff(trainll))
        self.assertTrue(np.all(np.diff(trainll) > -0.5))

        post_testll = [h.lpdf(x) for x in test_obs]
        if not (np.sum(post_testll) > np.sum(init_testll)):
            print
            print 'Test MAP train: %s (%s)\n  %s\n  %s' % (self.cvtype, params,
                                                           init_testll,
                                                           post_testll)
        self.assertTrue(np.sum(post_testll) > np.sum(init_testll))

    def test_train_covars(self):
        self.test_train('c')


class TestGaussianHMMMAPTrainerWithSphericalCovars(unittest.TestCase,
                                                   GaussianHMMMAPTrainerTester):
    cvtype = 'spherical'

    def test_train_startprob_and_transmat(self):
        self.test_train('st')

    def test_train_means(self):
        self.test_train('m')


class TestGaussianHMMMAPTrainerWithDiagonalCovars(unittest.TestCase,
                                                   GaussianHMMMAPTrainerTester):
    cvtype = 'diag'


class TestGaussianHMMMAPTrainerWithTiedCovars(unittest.TestCase,
                                              GaussianHMMMAPTrainerTester):
    cvtype = 'tied'


class TestGaussianHMMMAPTrainerWithFullCovars(unittest.TestCase,
                                              GaussianHMMMAPTrainerTester):
    cvtype = 'full'


if __name__ == '__main__':
    unittest.main()

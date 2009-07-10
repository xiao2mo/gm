import abc
import itertools
import time

import numpy as np
import matplotlib.pyplot as plt
import scipy as sp
import scipy.cluster

from generative_model import GenerativeModel
from gmm import *
from gmm import _distribute_covar_matrix_to_match_cvtype, _validate_covars

#implemented_classes = [_GaussianHMM, _GMMHMM]
#
#def HMM(emission_type='gaussian', *args, **kwargs):
def HMM(emission_type='gaussian', *args, **kwargs):

    supported_emission_types = dict([(x.emission_type, x)
                                     for x in _BaseHMM.__subclasses__()])
                                     #for x in implemented_classes])
    if emission_type == supported_emission_types.keys():
        return suppoerted_emission_types[emission_type](*args, **kwargs)
    else:
        raise ValueError, 'Unknown emission_type'


class _BaseHMM(GenerativeModel):
    """Hidden Markov Model abstract base class.
    
    See the instance documentation for details specific to a particular object.
    """
    __metaclass__ = abc.ABCMeta

    # This class implements the public interface to all HMMs that
    # derive from it, including all of the machinery for the
    # forward-backward and Viterbi algorithms.  Subclasses need only
    # implement the abstractproperty emission_type, and the
    # abstractmethods _generate_sample_from_state(),
    # _compute_obs_log_likelihood(), and _mstep() which depend on the
    # specific emission distribution.
    #
    # Subclasses will probably also want to implement their own init()
    # to initialize the emission distribution parameters (and any
    # corresponding properties to expose the parameters publically).

    @abc.abstractproperty
    def emission_type(self):
        """String identifier for the emission distribution used by this HMM"""
        pass

    def __init__(self, nstates=1):
        self._nstates = nstates

        self.startprob = np.tile(1.0 / nstates, nstates)
        self.transmat = np.tile(1.0 / nstates, (nstates, nstates))

    def eval(self, obs, maxrank=None, beamlogprob=-np.Inf):
        """Compute the log probability under the model and compute posteriors

        Parameters
        ----------
        obs : array_like, shape (n, ndim)
            Sequence of ndim-dimensional data points.  Each row
            corresponds to a single point in the sequence.

        Returns
        -------
        logprob : array_like, shape (n,)
            Log probabilities of each data point in `obs`
        posteriors: array_like, shape (n, nstates)
            Posterior probabilities of each state for each
            observation
        """
        obsll = self._compute_obs_log_likelihood(obs)
        logprob, fwdlattice = self._do_forward_pass(obsll, maxrank, beamlogprob)
        bwdlattice = self._do_backward_pass(obsll, fwdlattice, maxrank,
                                            beamlogprob)
        posteriors = np.exp(fwdlattice + bwdlattice,
                            np.tile(logprob[:,np.newaxis], (1, self._nstates)))
        return logprob, posteriors

    def lpdf(self, obs, maxrank=None, beamlogprob=-np.Inf):
        """Compute the log probability under the model.

        Parameters
        ----------
        obs : array_like, shape (n, ndim)
            Sequence of ndim-dimensional data points.  Each row
            corresponds to a single data point.

        Returns
        -------
        logprob : array_like, shape (n,)
            Log probabilities of each data point in `obs`
        """
        obsll = self._compute_obs_log_likelihood(obs)
        logprob, fwdlattice =  self._do_forward_pass(obsll, maxrank,
                                                     beamlogprob)
        return logprob

    def decode(self, obs):
        """Find most likely states for each point in `obs`.

        Parameters
        ----------
        obs : array_like, shape (n, ndim)
            List of ndim-dimensional data points.  Each row corresponds to a
            single data point.

        Returns
        -------
        components : array_like, shape (n,)
            Index of the most likelihod states for each observation
        """
        obsll = self._compute_obs_log_likelihood(obs)
        logprob, state_sequence = self._do_forward_pass_viterbi(obsll, maxrank,
                                                                beamlogprob)
        return state_sequence
        
    def rvs(self, n=1):
        """Generate random samples from the model.

        Parameters
        ----------
        n : int
            Number of samples to generate.

        Returns
        -------
        obs : array_like, length `n`
            List of samples
        """

        startprob_pdf = self.startprob
        startprob_cdf = np.cumsum(startprob_pdf)
        transmat_pdf = self.transmat
        transmat_cdf = np.cumsum(transmat_pdf, 1);

        # Initial state.
        rand = np.random.rand()
        currstate = (startprob_cdf > rand).argmax()
        obs = [self._generate_sample_from_state(currstate)]

        for x in xrange(n-1):
            rand = np.random.rand()
            currstate = (transmat_cdf[currstate] > rand).argmax()
            obs.append(self._generate_sample_from_state(currstate))

        return np.array(obs)

    def init(self, obs, params='stmc', **kwargs):
        """Initialize model parameters from data using the k-means algorithm

        Parameters
        ----------
        obs : array_like, shape (n, ndim)
            List of ndim-dimensional data points.  Each row corresponds to a
            single data point.
        params : string
            Controls which parameters are updated in the training
            process.  Can contain any combination of 's' for startprob,
            't' for transmat, 'm' for means, and 'c' for covars.
            Defaults to 'stmc'.
        **kwargs :
            Keyword arguments to pass through to the k-means function 
            (scipy.cluster.vq.kmeans2)

        See Also
        --------
        scipy.cluster.vq.kmeans2
        """
        if 's' in params:
            self.startprob = np.tile(1.0 / self._nstates, self._nstates)
        if 't' in params:
            shape = (self._nstates, self._nstates)
            self.startprob = np.tile(1.0 / self._nstates,  shape)

    def train(self, obs, iter=10, min_covar=1.0, verbose=False, thresh=1e-2,
              params='stmc'):
        """Estimate model parameters with the Baum-Welch algorithm.

        Parameters
        ----------
        obs : array_like, shape (n, ndim)
            List of ndim-dimensional data points.  Each row corresponds to a
            single data point.
        iter : int
            Number of EM iterations to perform.
        min_covar : float
            Floor on the diagonal of the covariance matrix to prevent
            overfitting.  Defaults to 1.0.
        verbose : bool
            Flag to toggle verbose progress reports.  Defaults to False.
        thresh : float
            Convergence threshold.
        params : string
            Controls which parameters are updated in the training
            process.  Can contain any combination of 's' for startprob,
            't' for transmat, 'm' for means, and 'c' for covars.
            Defaults to 'stmc'.

        Returns
        -------
        logprob : list
            Log probabilities of each data point in `obs` for each iteration
        """

        T = time.time()
        logprob = []
        for i in xrange(iter):
            # Expectation step
            curr_logprob,posteriors = self.eval(obs)
            logprob.append(curr_logprob.sum())

            if verbose:
                currT = time.time()
                print ('Iteration %d: log likelihood = %f (took %f seconds).'
                       % (i, logprob[-1], currT - T))
                T = currT

            # Check for convergence.
            if i > 0 and abs(logprob[-1] - logprob[-2]) < thresh:
                if verbose:
                    print 'Converged at iteration %d.' % i
                break

            # Maximization step
            self._mstep(posteriors)
        return logprob

    @property
    def nstates(self):
        """Number of states in the model."""
        return self._nstates

    @property
    def startprob(self):
        """Mixing startprob for each state."""
        return np.exp(self._log_startprob)

    @startprob.setter
    def startprob(self, startprob):
        if len(startprob) != self._nstates:
            raise ValueError, 'startprob must have length nstates'
        if not almost_equal(np.sum(startprob), 1.0):
            raise ValueError, 'startprob must sum to 1.0'
        
        self._log_startprob = np.log(np.array(startprob).copy())

    @property
    def transmat(self):
        """Matrix of transition probabilities."""
        return np.exp(self._log_transmat)

    @transmat.setter
    def transmat(self, transmat):
        if np.array(transmat).shape != (self._nstates, self._nstates):
            raise ValueError, 'transmat must have shape (nstates, nstates)'
        if not np.all(almost_equal(np.sum(transmat, axis=1), 1.0)):
            raise ValueError, 'each row of transmat must sum to 1.0'
        
        self._log_transmat = np.log(np.array(transmat).copy())

    def _do_forward_pass_viterbi(self, *args, **kwargs):
        logprob, lattice = _do_forward_pass(*args, fun=np.max, **kwards)

        # Do traceback.
        reverse_state_sequence = []
        s = lattice[-1].argmax()
        for frame in reversed(lattice[:-1]):
            reverse_state_sequence.append(s)
            s = frame[s]

        reverse_state_sequence.reverse()
        return logprob, reverse_state_sequence

    def _do_forward_pass(self, framelogprob, maxrank=None, beamlogprob=-np.Inf,
                         fun=logsum):
        fwdlattice = np.zeros(self._nstates, len(obsll))

        fwdlattice[0] = self._startprob + framelogprob[0]
        for n in xrange(1, len(framelogprob)):
            idx = self._prune_states(fwdlattice[n-1], maxrank, beamlogprob)
            pr = (self._log_transmat[idx]
                  + np.tile(fwdlattice[n,idx][:,np.newaxis],
                            (1, self._nstates)))
            fwdlattice[n] = fun(pr, axis=1) + framelogprob[n]
        fwdlattice[fwdlattice <= ZEROLOGPROB] = -np.Inf;

        return logsum(fwdlattice[-1]), fwdlattice

    def _do_backward_pass(self, framelogprob, fwdlattice, maxrank=None,
                          beamlogprob=-np.Inf):
        bwdlattice = np.zeros(self._nstates, len(obsll))
        for n in xrange(len(framelogprob) - 1, 0, -1):
            # Do HTK style pruning (p. 137 of HTK Book version 3.4).
            # Don't bother computing backward probability if
            # fwdlattice * bwdlattice is more than a certain distance
            # from the total log likelihood.
            idx = self._prune_states(bwdlattice[n] + alpha[n], None,
                                     -50)
                                     #beamlogprob)
                                     #-np.Inf)
            pr = (self._log_transmat[idx]
                  + np.tile((bwdlattice[n,idx]
                             + framelogprob[n,idx])[:,np.newaxis],
                            (1, self._nstates)))
            bwdlattice[n-1] = logsum(pr, axis=1)
        bwdlattice[bwdlattice <= ZEROLOGPROB] = -np.Inf;

        return bwdlattice

    def _prune_states(self, lattice_frame, maxrank, beamlogprob):
        """ Returns indices of the active states in `lattice_frame`
        after rank and beam pruning.
        """
        # Beam pruning
        threshlogprob = logsum(lattice_frame) + beamlogprob
        
        # Rank pruning
        if maxrank:
            # How big should our rank pruning histogram be?
            nbins = 3 * len(lattice_frame)

            lattice_min = lattice_frame[lattice_frame > ZEROLOGPROB].min() - 1
            hst, cdf = np.histogram(tmp, bins=nbins, new=True,
                                    range=(lattice_min, lattice_frame.max()))
        
            # Want to look at the high ranks.
            hst = hst[::-1]
            cdf = cdf[::-1]
    
            hst = hst.cumsum()
            rankthresh = cdf[hst.cumsum() >= maxrank].argmin()
      
            # Only change the threshold if it is stricter than the beam
            # threshold.
            threshlogprob = max(threshlogprob, rankthresh)
    
        # Which states are active?
        state_idx, = where(lattice_frame >= threshlogprob)

    @abc.abstractmethod
    def _compute_obs_log_likelihood(self, obs):
        pass
    
    @abc.abstractmethod
    def _generate_sample_from_state(self, state):
        pass

    @abc.abstractmethod
    def _mstep(self, obs, posteriors):
        pass



class _GaussianHMM(_BaseHMM):
    """Hidden Markov Model with Gaussian emissions

    Representation of a hidden Markov model probability distribution.
    This class allows for easy evaluation of, sampling from, and
    maximum-likelihood estimation of the parameters of a HMM.

    Attributes
    ----------
    cvtype : string (read-only)
        String describing the type of covariance parameters used by
        the model.  Must be one of 'spherical', 'tied', 'diag', 'full'.
    ndim : int (read-only)
        Dimensionality of the Gaussian components.
    nstates : int (read-only)
        Number of states in the model.
    transmat : array, shape (`nstates`, `nstates`)
        Matrix of transition probabilities between states.
    startprob : array, shape ('nstates`,)
        Initial state occupation distribution.
    means : array, shape (`nstates`, `ndim`)
        Mean parameters for each state.
    covars : array
        Covariance parameters for each state.  The shape depends on
        `cvtype`:
            (`nstates`,)                if 'spherical',
            (`ndim`, `ndim`)            if 'tied',
            (`nstates`, `ndim`)         if 'diag',
            (`nstates`, `ndim`, `ndim`) if 'full'

    Methods
    -------
    eval(obs)
        Compute the log likelihood of `obs` under the HMM.
    decode(obs)
        Find most likely state sequence for each point in `obs` using the
        Viterbi algorithm.
    rvs(n=1)
        Generate `n` samples from the HMM.
    init(obs)
        Initialize HMM parameters from `obs`.
    train(obs)
        Estimate HMM parameters from `obs` using the Baum-Welch algorithm.

    Examples
    --------
    >>> hmm = HMM(nstates=2, ndim=1)

    See Also
    --------
    model : Gaussian mixture model
    """

    @property
    def emission_type(self):
        return 'gaussian'

    def __init__(self, nstates=1, ndim=1, cvtype='diag'):
        """Create a hidden Markov model

        Initializes parameters such that every state has
        zero mean and identity covariance.

        Parameters
        ----------
        ndim : int
            Dimensionality of the states.
        nstates : int
            Number of states.
        cvtype : string (read-only)
            String describing the type of covariance parameters to
            use.  Must be one of 'spherical', 'tied', 'diag', 'full'.
            Defaults to 'diag'.
        """

        super(_GaussianHMM, self).__init__(nstates)
        self._ndim = ndim
        self._cvtype = cvtype
        self.means = np.zeros((nstates, ndim))
        self.covars = _distribute_covar_matrix_to_match_cvtype(
            np.eye(ndim), cvtype, nstates)

    def init(self, obs, params='stmc', **kwargs):
        """Initialize model parameters from data using the k-means algorithm

        Parameters
        ----------
        obs : array_like, shape (n, ndim)
            List of ndim-dimensional data points.  Each row corresponds to a
            single data point.
        params : string
            Controls which parameters are updated in the training
            process.  Can contain any combination of 's' for startprob,
            't' for transmat, 'm' for means, and 'c' for covars.
            Defaults to 'stmc'.
        **kwargs :
            Keyword arguments to pass through to the k-means function 
            (scipy.cluster.vq.kmeans2)

        See Also
        --------
        scipy.cluster.vq.kmeans2
        """

        super(_GaussianHMM, self).init(obs, params=params)

        if 'm' in params:
            self._means,tmp = sp.cluster.vq.kmeans2(obs, self._nstates, **kwargs)
        if 'c' in params:
            cv = np.cov(obs.T)
            if not cv.shape:
                cv.shape = (1, 1)
            self._covars = _distribute_covar_matrix_to_match_cvtype(
                cv, self._cvtype, self._nstates)

    # Read-only properties.
    @property
    def cvtype(self):
        """Covariance type of the model.

        Must be one of 'spherical', 'tied', 'diag', 'full'.
        """
        return self._cvtype

    @property
    def ndim(self):
        """Dimensionality of the states."""
        return self._ndim

    @property
    def means(self):
        """Mean parameters for each state."""
        return self._means

    @means.setter
    def means(self, means):
        means = np.array(means)
        if means.shape != (self._nstates, self._ndim):
            raise ValueError, 'means must have shape (nstates, ndim)'
        self._means = means.copy()

    @property
    def covars(self):
        """Covariance parameters for each state."""
        return self._covars

    @covars.setter
    def covars(self, covars):
        covars = np.array(covars)
        _validate_covars(covars, self._cvtype, self._nstates, self._ndim)
        self._covars = np.array(covars).copy()

    def _compute_obs_log_likelihood(self, obs):
        pass

    def _generate_sample_from_state(self, state):
        pass

    def _mstep():
        pass
    

class _GMMHMM(_BaseHMM):
    @property
    def emission_type(self):
        return 'gaussian'

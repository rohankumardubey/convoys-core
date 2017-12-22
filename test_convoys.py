import numpy
import random
import scipy.stats
from convoys import Exponential, Gamma, Weibull, Bootstrapper


def test_exponential_model(c=0.05, lambd=0.1, n=100000):
    # With a really long observation window, the rate should converge to the measured
    C = numpy.array([random.random() < c and scipy.stats.expon.rvs(scale=1.0/lambd) or 0.0 for x in range(n)])
    N = numpy.array([100 for converted_at in C])
    B = numpy.array([bool(converted_at > 0) for converted_at in C])
    c = numpy.mean(B)
    model = Exponential()
    model.fit(C, N, B)
    assert 0.95*c < model.params['c'] < 1.05*c
    assert 0.95*lambd < model.params['lambd'] < 1.05*lambd


def test_gamma_model(c=0.05, lambd=0.1, k=10.0, n=100000):
    C = numpy.array([random.random() < c and scipy.stats.gamma.rvs(a=k, scale=1.0/lambd) or 0.0 for x in range(n)])
    N = numpy.array([1000 for converted_at in C])
    B = numpy.array([bool(converted_at > 0) for converted_at in C])
    c = numpy.mean(B)
    model = Gamma()
    model.fit(C, N, B)
    assert 0.95*c < model.params['c'] < 1.05*c
    assert 0.95*lambd < model.params['lambd'] < 1.05*lambd
    assert 0.95*k < model.params['k'] < 1.05*k


def test_weibull_model(c=0.05, lambd=0.1, k=0.5, n=100000):
    def sample_weibull():
        # scipy.stats is garbage for this
        # exp(-(x * lambda)^k) = y
        return (-numpy.log(random.random())) ** (1.0/k) / lambd
    B = numpy.array([bool(random.random() < c) for x in range(n)])
    C = numpy.array([b and sample_weibull() or 1.0 for b in B])
    N = numpy.array([1000 for b in B])
    c = numpy.mean(B)
    model = Weibull()
    model.fit(C, N, B)
    assert 0.95*c < model.params['c'] < 1.05*c
    # TODO: figure out how to make L-BFGS-B run longer
    # assert 0.95*lambd < model.params['lambd'] < 1.05*lambd
    # assert 0.95*k < model.params['k'] < 1.05*k


def test_bootstrapped_exponential_model(c=0.05, lambd=0.1, n=10000):
    C = numpy.array([random.random() < c and scipy.stats.expon.rvs(scale=1.0/lambd) or 0.0 for x in range(n)])
    N = numpy.array([100 for converted_at in C])
    B = numpy.array([bool(converted_at > 0) for converted_at in C])
    c = numpy.mean(B)
    model = Bootstrapper('exponential')
    model.fit(C, N, B)
    y, y_lo, y_hi = model.predict_final(confidence_interval=True)
    c_lo = scipy.stats.beta.ppf(0.05, n*c, n*(1-c))
    c_hi = scipy.stats.beta.ppf(0.95, n*c, n*(1-c))
    assert 0.95*c < y < 1.05 * c
    assert 0.95*c_lo < y_lo < 1.05 * c_lo
    assert 0.95*c_hi < y_hi < 1.05 * c_hi
import abc
import bisect
import datetime
import lifelines
import math
import numpy
import random
import seaborn
import scipy.optimize
from scipy.special import gamma, gammainc
from matplotlib import pyplot


def get_timescale(t):
    if t >= datetime.timedelta(days=1):
        t_factor, t_unit = 1./(24*60*60), 'Days'
    elif t >= datetime.timedelta(hours=1):
        t_factor, t_unit = 1./(60*60), 'Hours'
    elif t >= datetime.timedelta(minutes=1):
        t_factor, t_unit = 1./60, 'Minutes'
    else:
        t_factor, t_unit = 1, 'Seconds'
    return t_factor, t_unit


def get_arrays(data, t_factor):
    C = [(converted_at - created_at).total_seconds() * t_factor if converted_at is not None else 0.0
         for created_at, converted_at, now in data]
    N = [(now - created_at).total_seconds() * t_factor
         for created_at, converted_at, now in data]
    B = [bool(converted_at is not None)
         for created_at, converted_at, now in data]
    return numpy.array(C), numpy.array(N), numpy.array(B)


class Model(abc.ABC):
    def __init__(self, params={}):
        self.params = params

    @abc.abstractmethod
    def fit(self, C, N, B):
        pass

    @abc.abstractmethod
    def predict(self, ts, confidence_interval=False):
        pass


class Basic(Model):
    def fit(self, C, N, B, n_limit=30):
        n, k = len(C), 0
        self.ts = [0]
        self.ns = [n]
        self.ks = [k]
        events = [(c, 1, 0) for c, n, b in zip(C, N, B) if b] + \
                 [(n, -int(b), -1) for c, n, b in zip(C, N, B)]
        for t, k_delta, n_delta in sorted(events):
            k += k_delta
            n += n_delta
            self.ts.append(t)
            self.ks.append(k)
            self.ns.append(n)
            if n < n_limit:
                break

    def predict(self, ts, confidence_interval=False):
        js = [bisect.bisect_left(self.ts, t) for t in ts]
        ks = numpy.array([self.ks[j] if j < len(self.ks) else float('nan') for j in js])
        ns = numpy.array([self.ns[j] if j < len(self.ns) else float('nan') for j in js])
        if confidence_interval:
            return ks / ns, scipy.stats.beta.ppf(0.05, ks, ns-ks), scipy.stats.beta.ppf(0.95, ks, ns-ks)
        else:
            return ks / ns


class KaplanMeier(Model):
    def fit(self, C, N, B):
        T = [c if b else n for c, n, b in zip(C, N, B)]
        kmf = lifelines.KaplanMeierFitter()
        kmf.fit(T, event_observed=B)
        self.ts = kmf.survival_function_.index.values
        self.ps = 1.0 - kmf.survival_function_['KM_estimate'].values
        self.ps_hi = 1.0 - kmf.confidence_interval_['KM_estimate_lower_0.95'].values
        self.ps_lo = 1.0 - kmf.confidence_interval_['KM_estimate_upper_0.95'].values

    def predict(self, ts, confidence_interval=False):
        js = [bisect.bisect_left(self.ts, t) for t in ts]
        def array_lookup(a):
            return numpy.array([a[j] if j < len(a) else float('nan') for j in js])
        if confidence_interval:
            return (array_lookup(self.ps), array_lookup(self.ps_lo), array_lookup(self.ps_hi))
        else:
            return array_lookup(self.ps)


class Exponential(Model):
    def fit(self, C, N, B):
        def f(x):
            c, lambd = x
            neg_LL, neg_LL_deriv_c, neg_LL_deriv_lambd = 0, 0, 0
            likelihood_observed = c * lambd * numpy.exp(-lambd*C)
            likelihood_censored = (1 - c) + c * numpy.exp(-lambd*N)
            neg_LL = -numpy.sum(numpy.log(B * likelihood_observed + (1 - B) * likelihood_censored))
            neg_LL_deriv_c = -numpy.sum(B * 1/c + (1 - B) * (-1 + numpy.exp(-lambd*N)) / likelihood_censored)
            neg_LL_deriv_lambd = -numpy.sum(B * (1/lambd - T) + (1 - B) * (c * -T * numpy.exp(-lambd*N)) / likelihood_censored)
            return neg_LL, numpy.array([neg_LL_deriv_c, neg_LL_deriv_lambd])

        c_initial = numpy.mean(B)
        lambd_initial = 1.0 / max(N)
        lambd_max = 30.0 / max(N)
        lambd = self.params.get('lambd')
        res = scipy.optimize.minimize(
            fun=f,
            x0=(c_initial, lambd_initial),
            bounds=((1e-4, 1-1e-4),
                    (lambd, lambd) if lambd else (1e-4, lambd_max)),
            method='L-BFGS-B',
            jac=True)
        c, lambd = res.x
        self.params = dict(c=c, lambd=lambd)

    def predict(self, t):
        c, lambd = self.params['c'], self.params['lambd']
        return c * (1 - numpy.exp(-t * lambd))


class Gamma(Model):
    def fit(self, C, N, B):
        # TODO(erikbern): should compute Jacobian of this one
        def f(x):
            c, lambd, k = x
            neg_LL = 0
            # PDF of gamma: 1.0 / gamma(k) * lambda ^ k * t^(k-1) * exp(-t * lambda)
            likelihood_observed = c * 1/gamma(k) * lambd**k * C**(k-1) * numpy.exp(-lambd*C)
            # CDF of gamma: 1.0 / gamma(k) * gammainc(k, lambda * t)
            likelihood_censored = (1 - c) + c * (1 - gammainc(k, lambd*N))
            neg_LL = -numpy.sum(numpy.log(B * likelihood_observed + (1 - B) * likelihood_censored))
            return neg_LL

        c_initial = numpy.mean(B)
        lambd_initial = 1.0 / max(N)
        lambd_max = 30.0 / max(N)
        k_initial = 10.0
        lambd = self.params.get('lambd')
        k = self.params.get('k')
        res = scipy.optimize.minimize(
            fun=f,
            x0=(c_initial, lambd_initial, k_initial),
            bounds=((1e-4, 1-1e-4),
                    (lambd, lambd) if lambd else (1e-4, lambd_max),
                    (k, k) if k else (1.0, 30.0)),
            method='L-BFGS-B')
        c, lambd, k = res.x
        self.params = dict(c=c, lambd=lambd, k=k)

    def predict(self, t):
        c, lambd, k = self.params['c'], self.params['lambd'], self.params['k']
        return c * gammainc(k, lambd*t)


class Bootstrapper(Model):
    def __init__(self, base_fitter, n_bootstraps=100):
        self.models = [base_fitter() for i in range(n_bootstraps)]

    def fit(self, C, N, B):
        CNB = list(zip(C, N, B))
        for model in self.models:
            CNB_bootstrapped = [random.choice(CNB) for _ in CNB]
            C_bootstrapped = numpy.array([c for c, n, b in CNB_bootstrapped])
            N_bootstrapped = numpy.array([n for c, n, b in CNB_bootstrapped])
            B_bootstrapped = numpy.array([b for c, n, b in CNB_bootstrapped])
            model.fit(C_bootstrapped, N_bootstrapped, B_bootstrapped)

    def predict(self, ts, confidence_interval=False):
        all_ts = numpy.array([model.predict(ts) for model in self.models])
        if confidence_interval:
            return (numpy.mean(all_ts, axis=0),
                    numpy.percentile(all_ts, 5, axis=0),
                    numpy.percentile(all_ts, 95, axis=0))
        else:
            return numpy.mean(all_ts, axis=0)


def plot_conversion(data, t_max=None, title=None, group_min_size=0, max_groups=100, model='basic', share_params=False):
    # Set x scale
    if t_max is None:
        t_max = max(now - created_at for group, created_at, converted_at, now in data)
    t_factor, t_unit = get_timescale(t_max)
    t_max = t_max.total_seconds() * t_factor

    # Split data by group
    js = {}
    for group, created_at, converted_at, now in data:
        if converted_at is not None and converted_at < created_at:
            print('created at', created_at, 'but converted at', converted_at)
            continue
        js.setdefault(group, []).append((created_at, converted_at, now))

    # Remove groups with too few data points
    groups = [group for group, data_points in js.items() if len(data_points) >= group_min_size]

    # Require at least one conversion per group
    groups = [group for group, data_points in js.items() if any(converted_at for _, converted_at, _ in data_points) > 0]

    # Pick the top groups
    groups = sorted(groups, key=lambda group: len(js[group]), reverse=True)[:max_groups]

    # Sort groups lexicographically
    groups = sorted(groups)

    if share_params:
        # TODO: Pool data and fit shared parameters for all models if requested
        raise
    else:
        shared_params = {}

    # PLOT
    colors = seaborn.color_palette('hls', len(groups))
    y_max = 0
    for group, color in zip(sorted(groups), colors):
        C, N, B = get_arrays(js[group], t_factor)
        if model == 'basic':
            m = Basic()
        elif model == 'kaplan-meier':
            m = KaplanMeier()
        elif model == 'exponential':
            m = Bootstrapper(lambda: Exponential(params=shared_params))
        elif model == 'gamma':
            m = Bootstrapper(lambda: Gamma(params=shared_params))
        m.fit(C, N, B)

        label = '%s (n=%.0f, k=%.0f)' % (group, len(B), sum(B))
        t = numpy.linspace(0, t_max, 1000)
        p, p_lo, p_hi = m.predict(t, confidence_interval=True)
        y_max = max(y_max, 90. * max(p_hi), 110. * max(p))
        
        pyplot.plot(t, 100. * p, color=color, label=label)
        pyplot.fill_between(t, 100. * p_lo, 100. * p_hi, color=color, alpha=0.2)

    if title:
        pyplot.title(title)
    pyplot.xlim([0, t_max])
    pyplot.ylim([0, y_max])
    pyplot.xlabel(t_unit)
    pyplot.ylabel('Conversion rate %')
    pyplot.legend()
    pyplot.gca().grid(True)
    pyplot.tight_layout()

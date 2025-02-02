# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""Calculate the exposure correction for light curves.

Only works for data taken in specific data modes of NuSTAR, where all events
are telemetered.
"""
from __future__ import (absolute_import, unicode_literals, division,
                        print_function)

import numpy as np
from .io import load_events_and_gtis
from .io import get_file_type, save_lcurve, MP_FILE_EXTENSION
from .base import create_gti_mask, mp_root, _assign_value_if_none
import logging
import warnings


def get_livetime_per_bin(times, events, priors, dt=None, gti=None):
    """Get the livetime in a series of time intervals.

    Parameters
    ----------
    times : array-like
        The array of times to look at
    events : array-like
        A list of events, producing dead time
    priors : array-like
        The livetime before each event (as in the PRIOR column of unfiltered
        NuSTAR event files)

    Returns
    -------
    livetime_array : array-like
        An array of the same length as times, containing the live time values

    Other Parameters
    ----------------
    dt : float or array-like
        The width of the time bins of the time array. Can be a single float or
        an array of the same length as times
    gti : [[g0_0, g0_1], [g1_0, g1_1], ...]
         Good time intervals. Defaults to
         [[time[0] - dt[0]/2, time[-1] + dt[-1]/2]]

    """
    assert len(events) == len(priors), \
        "`events` and `priors` must be of the same length"

    dt = _assign_value_if_none(dt, np.median(np.diff(times)))

    try:
        len(dt)
    except:
        dt = dt + np.zeros(len(times))

    # Floating point events, starting from events[0]
    ev_fl = np.array(events - events[0], dtype=np.float64)
    pr_fl = np.array(priors, dtype=np.float64)

    # Start of livetime
    livetime_starts = ev_fl - pr_fl

    # Time bin borders: start from half a bin before tstart, end half a bin
    # after tstop
    tbins = np.array(
        np.append(times - dt / 2, [times[-1] + dt[-1] / 2]) - events[0],
        dtype=np.float64)

    tbin_starts = tbins[:-1]

    # Filter points outside of range of light curve
    filter = (ev_fl > tbins[0]) & (livetime_starts < tbins[-1])
    ev_fl = ev_fl[filter]
    pr_fl = pr_fl[filter]
    livetime_starts = livetime_starts[filter]

    livetime_array = np.zeros_like(times)

    # ------ Normalize priors at the start and end of light curve ----------
    before_start = \
        (livetime_starts < tbin_starts[0]) & (ev_fl > tbin_starts[0])

    livetime_starts[before_start] = tbins[0] + 1e-9
    pr_fl[before_start] = ev_fl[before_start] - livetime_starts[before_start]

    after_end = \
        (livetime_starts < tbins[-1]) & (ev_fl > tbins[-1])
    ev_fl[after_end] = tbins[-1] - 1e-9
    pr_fl[after_end] = ev_fl[after_end] - livetime_starts[after_end]

    # ----------------------------------------------------------------------

    # Find bins to which "livetime starts" and "events" belong
    lts_bin = np.searchsorted(tbin_starts, livetime_starts, 'right') - 1
    ev_bin = np.searchsorted(tbin_starts, ev_fl, 'right') - 1

    # First of all, just consider livetimes and events inside the same bin.
    first_pass = ev_bin == lts_bin
    expo, bins = np.histogram(ev_fl[first_pass], bins=tbins,
                              weights=pr_fl[first_pass])

    assert np.all(expo) >= 0, expo
    livetime_array += expo

    # Now, let's consider the case where livetime starts some bins before.
    # We start from the most distant (max_bin_diff) and we arrive to 1.
    max_bin_diff = np.max(ev_bin - lts_bin)

    for bin_diff in range(max_bin_diff, 0, -1):
        idxs = ev_bin == lts_bin + bin_diff
        # Filter only events relevant to this case
        ev_bin_good = ev_bin[idxs]
        lts_bin_good = lts_bin[idxs]
        ev_good = ev_fl[idxs]
        lt_good = livetime_starts[idxs]

        # find corresponding time bins
        e_idx = np.searchsorted(tbin_starts, ev_good, 'right') - 1
        _tbins = tbin_starts[e_idx]
        livetime_array[ev_bin_good] += ev_good - _tbins
        assert np.all(ev_good - _tbins >= 0), \
            "Invalid boundaries. Contact the developer: {}".format(
                ev_good - _tbins)

        l_idx = np.searchsorted(tbin_starts, lt_good, 'right')
        _tbins = tbin_starts[l_idx]
        livetime_array[lts_bin_good] += _tbins - lt_good
        assert np.all(_tbins - lt_good >= 0), \
            "Invalid boundaries. Contact the developer: {}".format(
                _tbins - lt_good)

        # Complete bins
        if bin_diff > 1:
            for i in range(1, bin_diff):
                livetime_array[lts_bin_good + i] += \
                    dt[lts_bin_good + i]

    return livetime_array


def _plot_dead_time_from_uf(uf_file, outroot="expo"):
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec
    from numpy import histogram

    additional_columns = ["PRIOR", "PI", "SHIELD",
                          "SHLD_T", "SHLD_HI"]

    data = load_events_and_gtis(uf_file,
                                additional_columns=additional_columns)

    events = data.ev_list
    additional = data.additional_data

    priors = additional["PRIOR"]

    dead_times = np.diff(events) - priors[1:]
    shields = additional["SHIELD"][1:]
    shld_t = additional["SHLD_T"][1:]
    shld_hi = additional["SHLD_HI"][1:]

    bins = np.percentile(dead_times, np.linspace(0, 100, 1000))
    hist_all, bins_all = histogram(dead_times, bins=bins, density=True)
    hist_shield, bins_shield = histogram(dead_times[shields > 0], bins=bins,
                                         density=True)
    hist_noshield, bins_noshield = histogram(dead_times[shields == 0],
                                             bins=bins, density=True)
    hist_shld_hi, bins_shld_hi = histogram(dead_times[shld_hi > 0],
                                           bins=bins, density=True)

    bin_centers = bins[:-1] + np.diff(bins) / 2
    fig = plt.figure("Dead time distribution", figsize=(10, 10))
    gs = GridSpec(2, 1, hspace=0)
    ax1 = plt.subplot(gs[0])
    ax1.loglog(bin_centers, hist_all, drawstyle="steps-mid", label="all")
    ax1.loglog(bin_centers, hist_shield, drawstyle="steps-mid", label="shield")
    ax1.loglog(bin_centers, hist_shld_hi, drawstyle="steps-mid",
               label="shld_hi")
    ax1.loglog(bin_centers, hist_noshield, drawstyle="steps-mid",
               label="no shield")
    ax1.set_ylabel("Occurrences (arbitrary units)")
    ax1.legend()
    ax2 = plt.subplot(gs[1], sharex=ax1)

    for sht in set(shld_t[shld_t > 0]):
        hs, bs = histogram(dead_times[shld_t == sht], bins=bins, density=True)
        ax2.loglog(bin_centers, hs, drawstyle="steps-mid",
                   label="shield time {}".format(sht))
    ax2.set_xlabel("Dead time (s)")
    ax2.set_ylabel("Occurrences (arbitrary units)")
    ax2.legend()
    plt.draw()
    fig.savefig(outroot + "_deadt_distr.png")


def get_exposure_from_uf(time, uf_file, dt=None, gti=None):
    """Get livetime from unfiltered event file.

    Parameters
    ----------
    time : array-like
        The time bins of the light curve
    uf_file : str
        Unfiltered event file (the one in the event_cl directory with the _uf
        suffix)

    Returns
    -------
    expo : array-like
        Exposure (livetime) values corresponding to time bins

    Other Parameters
    ----------------
    dt : float
        If time array is not sampled uniformly, dt can be specified here.

    """
    dt = _assign_value_if_none(dt, np.median(np.diff(time)))

    additional_columns = ["PRIOR", "PI"]

    data = load_events_and_gtis(uf_file,
                                additional_columns=additional_columns)

    events = data.ev_list
    additional = data.additional_data

    priors = additional["PRIOR"]
    # grade = additional["GRADE"]
    # pis = additional["PI"]
    # xs = additional["X"]
    # ys = additional["Y"]
    #
    # filt = (grade < 32) & (pis >= 0) & (x is not None) & (y is not None)

    expo = get_livetime_per_bin(time, events, priors, dt, gti=gti)

    return expo


def _plot_corrected_light_curve(time, lc, expo, gti=None, outroot="expo"):
    import matplotlib.pyplot as plt
    good = create_gti_mask(time, gti)
    fig = plt.figure("Exposure-corrected lc")
    plt.plot(time[good], expo[good] / np.max(expo) * np.max(lc[good]),
             label="Exposure (arbitrary units)", zorder=10)
    plt.plot(time[good], lc[good], label="Light curve", zorder=20)
    plt.plot(time[good], lc[good] / expo[good],
             label="Exposure-corrected Light curve")
    plt.legend()
    fig.savefig(outroot + "_corr_lc.png")


def correct_lightcurve(lc_file, uf_file, outname=None, expo_limit=1e-7):
    """Apply exposure correction to light curve.

    Parameters
    ----------
    lc_file : str
        The light curve file, in MaLTPyNT format
    uf_file : str
        The unfiltered event file, in FITS format

    Returns
    -------
    outdata : str
        Output data structure

    Other Parameters
    ----------------
    outname : str
        Output file name
    """
    outname = _assign_value_if_none(
        outname, mp_root(lc_file) + "_lccorr" + MP_FILE_EXTENSION)

    ftype, contents = get_file_type(lc_file)

    time = contents["time"]
    lc = contents["lc"]
    dt = contents["dt"]
    gti = contents["GTI"]

    expo = get_exposure_from_uf(time, uf_file, dt=dt, gti=gti)

    outdata = contents.copy()

    newlc = np.array(lc / expo * dt, dtype=np.float64)
    newlc[expo < expo_limit] = 0
    outdata["lc"] = newlc
    outdata["expo"] = expo

    save_lcurve(outdata, outname)
    return outdata


def main(args=None):
    """Main function called by the `MPexposure` command line script."""
    import argparse
    description = (
        'Create exposure light curve based on unfiltered event files.')
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("lcfile", help="Light curve file (MaltPyNT format)")
    parser.add_argument("uffile", help="Unfiltered event file (FITS)")
    parser.add_argument("-o", "--outroot", type=str, default=None,
                        help='Root of output file names')

    parser.add_argument("--loglevel",
                        help=("use given logging level (one between INFO, "
                              "WARNING, ERROR, CRITICAL, DEBUG; "
                              "default:WARNING)"),
                        default='WARNING',
                        type=str)
    parser.add_argument("--debug", help="use DEBUG logging level",
                        default=False, action='store_true')
    parser.add_argument("--plot", help="Plot on window",
                        default=False, action='store_true')

    args = parser.parse_args(args)

    if args.debug:
        args.loglevel = 'DEBUG'

    numeric_level = getattr(logging, args.loglevel.upper(), None)
    logging.basicConfig(filename='MPexposure.log', level=numeric_level,
                        filemode='w')

    lc_file = args.lcfile
    uf_file = args.uffile

    outroot = _assign_value_if_none(args.outroot, mp_root(lc_file))

    outname = outroot + "_lccorr" + MP_FILE_EXTENSION

    outdata = correct_lightcurve(lc_file, uf_file, outname)

    time = outdata["time"]
    lc = outdata["lc"]
    expo = outdata["expo"]
    gti = outdata["GTI"]

    try:
        _plot_corrected_light_curve(time, lc * expo, expo, gti, outroot)
        _plot_dead_time_from_uf(uf_file, outroot)
    except Exception as e:
        warnings.warn(str(e))
        pass

    if args.plot:
        import matplotlib.pyplot as plt
        plt.show()

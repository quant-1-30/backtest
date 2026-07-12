#! /usr/bin/env python  
# -*- coding: utf-8 -*-

import h5py
import numpy as np
import pandas as pd
from functools import partial

VERSION = 0

DATA = 'data'
INDEX = 'index'
LIFETIMES = 'lifetimes'
CURRENCY = 'currency'
CODE = 'code'

SCALING_FACTOR = 'scaling_factor'

OPEN = 'open'
HIGH = 'high'
LOW = 'low'
CLOSE = 'close'
VOLUME = 'volume'

FIELDS = (OPEN, HIGH, LOW, CLOSE, VOLUME)

DAY = 'day'
SID = 'sid'

START_DATE = 'start_date'
END_DATE = 'end_date'


DEFAULT_SCALING_FACTORS = {
    # Retain 3 decimal places for prices.
    OPEN: 1000,
    HIGH: 1000,
    LOW: 1000,
    CLOSE: 1000,
    # Volume is expected to be a whole integer.
    VOLUME: 1,
}


def coerce_to_uint32(a, scaling_factor):
    """
    Returns a copy of the array as uint32, applying a scaling factor to
    maintain precision if supplied.
    """
    return (a * scaling_factor).round().astype('uint32')


def compute_asset_lifetimes(frames):
    """
    Parameters
    ----------
    frames : dict[str, pd.DataFrame]
        A dict mapping each OHLCV field to a dataframe with a row for
        each date and a column for each sid, as passed to write().

    Returns
    -------
    start_date_ixs : np.array[int64]
        The index of the first date with non-nan values, for each sid.
    end_date_ixs : np.array[int64]
        The index of the last date with non-nan values, for each sid.
    """
    # Build a 2D array (dates x sids), where an entry is True if all
    # fields are nan for the given day and sid.
    is_null_matrix = np.logical_and.reduce(
        [frames[field].isnull().values for field in FIELDS],
    )
    if not is_null_matrix.size:
        empty = np.array([], dtype='int64')
        return empty, empty.copy()

    # Offset of the first null from the start of the input.
    start_date_ixs = is_null_matrix.argmin(axis=0)
    # Offset of the last null from the **end** of the input.
    end_offsets = is_null_matrix[::-1].argmin(axis=0)
    # Offset of the last null from the start of the input
    end_date_ixs = is_null_matrix.shape[0] - end_offsets - 1

    return start_date_ixs, end_date_ixs


def convert_price_with_scaling_factor(a, scaling_factor):
    conversion_factor = (1.0 / scaling_factor)

    zeroes = (a == 0)
    return np.where(zeroes, np.nan, a.astype('float64')) * conversion_factor


class HDF5DailyBarReader:
    """
        HDF5 Pricing File Format
        ------------------------
        At the top level, the file is keyed by country (to support regional
        files containing multiple countries).

        Within each country, there are 4 subgroups:

        ``/data``
        ^^^^^^^^^
        Each field (OHLCV) is stored in a dataset as a 2D array, with a row per
        sid and a column per session. This differs from the more standard
        orientation of dates x sids, because it allows each compressed block to
        contain contiguous values for the same sid, which allows for better
        compression.

        .. code-block:: none

           /data
             /open
             /high
             /low
             /close
             /volume

        ``/index``
        ^^^^^^^^^^
        Contains two datasets, the index of sids (aligned to the rows of the
        OHLCV 2D arrays) and index of sessions (aligned to the columns of the
        OHLCV 2D arrays) to use for lookups.

        .. code-block:: none

           /index
             /sid
             /day

        ``/lifetimes``
        ^^^^^^^^^^^^^^
        Contains two datasets, start_date and end_date, defining the lifetime
        for each asset, aligned to the sids index.

        .. code-block:: none

           /lifetimes
             /start_date
             /end_date



        Example
        ^^^^^^^
        Sample layout of the full file with multiple countries.

        .. code-block:: none

           |- /US
           |  |- /data
           |  |  |- /open
           |  |  |- /high
           |  |  |- /low
           |  |  |- /close
           |  |  |- /volume
           |  |
           |  |- /index
           |  |  |- /sid
           |  |  |- /day
           |  |
           |  |- /lifetimes
           |  |  |- /start_date
           |  |  |- /end_date

    """

    def __init__(self, field_group):
        # field_group : h5py.Group The group for a single country in an HDF5 daily pricing file.
        self._field_group = field_group 

        self._postprocessors = {
            OPEN: partial(convert_price_with_scaling_factor,
                          scaling_factor=self._read_scaling_factor(OPEN)),
            HIGH: partial(convert_price_with_scaling_factor,
                          scaling_factor=self._read_scaling_factor(HIGH)),
            LOW: partial(convert_price_with_scaling_factor,
                         scaling_factor=self._read_scaling_factor(LOW)),
            CLOSE: partial(convert_price_with_scaling_factor,
                           scaling_factor=self._read_scaling_factor(CLOSE)),
            VOLUME: lambda a: a,
        }

    @classmethod
    def from_file(cls, h5_file, field_code):
        """
        Construct from an h5py.File and a country code.

        Parameters
        ----------
        h5_file : h5py.File
            An HDF5 daily pricing file.
        field_code : str
            The field code for the field to read.
        """
        if h5_file.attrs['version'] != VERSION:
            raise ValueError(
                'mismatched version: file is of version %s, expected %s' % (
                    h5_file.attrs['version'],
                    VERSION,
                ),
            )

        return cls(h5_file[field_code])

    @classmethod
    def from_path(cls, path, field_code):
        """
        Construct from a file path and a country code.

        Parameters
        ----------
        path : str
            The path to an HDF5 daily pricing file.
        field_code : str
            The field code for the field to read.
        """
        return cls.from_file(h5py.File(path), field_code)

    def _read_scaling_factor(self, field):
        return self._field_group[field].attrs[SCALING_FACTOR]

    def load_raw_arrays(self,
                        columns,
                        start_date,
                        end_date,
                        assets):
        """
        Parameters
        ----------
        columns : list of str
           'open', 'high', 'low', 'close', or 'volume'
        start_date: Timestamp
           Beginning of the window range.
        end_date: Timestamp
           End of the window range.
        assets : list of int
           The asset identifiers in the window.

        Returns
        -------
        list of np.ndarray
            A list with an entry per field of ndarrays with shape
            (minutes in range, sids) with a dtype of float64, containing the
            values for the respective field over start and end dt range.
        """
        self._validate_timestamp(start_date)
        self._validate_timestamp(end_date)

        start = start_date.asm8
        end = end_date.asm8
        date_slice = self._compute_date_range_slice(start, end)
        n_dates = date_slice.stop - date_slice.start

        # Create a buffer into which we'll read data from the h5 file.
        # Allocate an extra row of space that will always contain null values.
        # We'll use that space to provide "data" for entries in ``assets`` that
        # are unknown to us.
        full_buf = np.zeros((len(self.sids) + 1, n_dates), dtype=np.uint32)
        # We'll only read values into this portion of the read buf.
        mutable_buf = full_buf[:-1]

        # Indexer that converts an array aligned to self.sids (which is what we
        # pull from the h5 file) into an array aligned to ``assets``.
        #
        # Unknown assets will have an index of -1, which means they'll always
        # pull from the last row of the read buffer. We allocated an extra
        # empty row above so that these lookups will cause us to fill our
        # output buffer with "null" values.
        sid_selector = self._make_sid_selector(assets)

        out = []
        for column in columns:
            # Zero the buffer to prepare to receive new data.
            mutable_buf.fill(0)

            dataset = self._country_group[DATA][column]

            # Fill the mutable portion of our buffer with data from the file.
            dataset.read_direct(
                mutable_buf,
                np.s_[:, date_slice],
            )

            # Select data from the **full buffer**. Unknown assets will pull
            # from the last row, which is always empty.
            out.append(self._postprocessors[column](full_buf[sid_selector].T))

        return out

    def _make_sid_selector(self, assets):
        """
        Build an indexer mapping ``self.sids`` to ``assets``.

        Parameters
        ----------
        assets : list[int]
            List of assets requested by a caller of ``load_raw_arrays``.

        Returns
        -------
        index : np.array[int64]
            Index array containing the index in ``self.sids`` for each location
            in ``assets``. Entries in ``assets`` for which we don't have a sid
            will contain -1. It is caller's responsibility to handle these
            values correctly.
        """
        assets = np.array(assets)
        sid_selector = self.sids.searchsorted(assets)
        unknown = np.in1d(assets, self.sids, invert=True)
        sid_selector[unknown] = -1
        return sid_selector

    def _compute_date_range_slice(self, start_date, end_date):
        # Get the index of the start of dates for ``start_date``.
        start_ix = self.dates.searchsorted(start_date)

        # Get the index of the start of the first date **after** end_date.
        end_ix = self.dates.searchsorted(end_date, side='right')

        return slice(start_ix, end_ix)

    def _validate_assets(self, assets):
        """Validate that asset identifiers are contained in the daily bars.

        Parameters
        ----------
        assets : array-like[int]
           The asset identifiers to validate.

        Raises
        ------
        NoDataForSid
            If one or more of the provided asset identifiers are not
            contained in the daily bars.
        """
        missing_sids = np.setdiff1d(assets, self.sids)

        if len(missing_sids):
            # raise NoDataForSid(
            #     'Assets not contained in daily pricing file: {}'.format(
            #         missing_sids
            #     )
            # )
            raise ValueError(f'Assets not contained in daily pricing file: {missing_sids}')

    def _validate_timestamp(self, ts):
        if ts.asm8 not in self.dates:
            # raise NoDataOnDate(ts)
            raise ValueError(f'Timestamp not contained in daily pricing file: {ts}')

    # @lazyval
    def dates(self):
        return self._country_group[INDEX][DAY][:].astype('datetime64[ns]')

    # @lazyval
    def sids(self):
        return self._country_group[INDEX][SID][:].astype('int64', copy=False)

    # @lazyval
    def asset_start_dates(self):
        return self.dates[self._country_group[LIFETIMES][START_DATE][:]]

    # @lazyval
    def asset_end_dates(self):
        return self.dates[self._country_group[LIFETIMES][END_DATE][:]]

    @property
    def last_available_dt(self):
        """
        Returns
        -------
        dt : pd.Timestamp
            The last session for which the reader can provide data.
        """
        return pd.Timestamp(self.dates[-1], tz='UTC')

    @property
    def trading_calendar(self):
        """
        Returns the zipline.utils.calendar.trading_calendar used to read
        the data.  Can be None (if the writer didn't specify it).
        """
        raise NotImplementedError(
            'HDF5 pricing does not yet support trading calendars.'
        )

    @property
    def first_trading_day(self):
        """
        Returns
        -------
        dt : pd.Timestamp
            The first trading day (session) for which the reader can provide
            data.
        """
        return pd.Timestamp(self.dates[0], tz='UTC')

    # @lazyval
    def sessions(self):
        """
        Returns
        -------
        sessions : DatetimeIndex
           All session labels (unioning the range for all assets) which the
           reader can provide.
        """
        return pd.to_datetime(self.dates, utc=True)

    def get_value(self, sid, dt, field):
        """
        Retrieve the value at the given coordinates.

        Parameters
        ----------
        sid : int
            The asset identifier.
        dt : pd.Timestamp
            The timestamp for the desired data point.
        field : string
            The OHLVC name for the desired data point.

        Returns
        -------
        value : float|int
            The value at the given coordinates, ``float`` for OHLC, ``int``
            for 'volume'.

        Raises
        ------
        NoDataOnDate
            If the given dt is not a valid market minute (in minute mode) or
            session (in daily mode) according to this reader's tradingcalendar.
        """
        self._validate_assets([sid])
        self._validate_timestamp(dt)

        sid_ix = self.sids.searchsorted(sid)
        dt_ix = self.dates.searchsorted(dt.asm8)

        value = self._postprocessors[field](
            self._country_group[DATA][field][sid_ix, dt_ix]
        )

        # When the value is nan, this dt may be outside the asset's lifetime.
        # If that's the case, the proper NoDataOnDate exception is raised.
        # Otherwise (when there's just a hole in the middle of the data), the
        # nan is returned.
        if np.isnan(value):
            if dt.asm8 < self.asset_start_dates[sid_ix]:
                # raise NoDataBeforeDate()
                raise ValueError(f'No data before date: {dt}')

            if dt.asm8 > self.asset_end_dates[sid_ix]:
                # raise NoDataAfterDate()
                raise ValueError(f'No data after date: {dt}')

        return value

    def get_last_traded_dt(self, asset, dt):
        """
        Get the latest day on or before ``dt`` in which ``asset`` traded.

        If there are no trades on or before ``dt``, returns ``pd.NaT``.

        Parameters
        ----------
        asset : zipline.asset.Asset
            The asset for which to get the last traded day.
        dt : pd.Timestamp
            The dt at which to start searching for the last traded day.

        Returns
        -------
        last_traded : pd.Timestamp
            The day of the last trade for the given asset, using the
            input dt as a vantage point.
        """
        sid_ix = self.sids.searchsorted(asset.sid)
        # Used to get a slice of all dates up to and including ``dt``.
        dt_limit_ix = self.dates.searchsorted(dt.asm8, side='right')

        # Get the indices of all dates with nonzero volume.
        nonzero_volume_ixs = np.ravel(
            np.nonzero(self._country_group[DATA][VOLUME][sid_ix, :dt_limit_ix])
        )

        if len(nonzero_volume_ixs) == 0:
            return pd.NaT

        return pd.Timestamp(self.dates[nonzero_volume_ixs][-1], tz='UTC')


def check_sids_arrays_match(left, right, message):
    """Check that two 1d arrays of sids are equal
    """
    if len(left) != len(right):
        raise ValueError(
            "{}:\nlen(left) ({}) != len(right) ({})".format(
                message, len(left), len(right)
            )
        )

    diff = (left != right)
    if diff.any():
        (bad_locs,) = np.where(diff)
        raise ValueError(
            "{}:\n Indices with differences: {}".format(message, bad_locs)
        )

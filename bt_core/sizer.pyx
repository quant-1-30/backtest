
from libcpp.string cimport string as cpp_string
from libcpp.unordered_map cimport unordered_map

from bt_protocol._protocol import SnapshotBody


cdef class Sizer:
    '''This is the base class for *Sizers*. Any *sizer* should subclass this
    and override the ``_getsizing`` method

    Member Attribs:

      - ``strategy``: will be set by the strategy in which the sizer is working

        Gives access to the entire api of the strategy, for example if the
        actual data position would be needed in ``_getsizing``::

           position = self.strategy.getposition(data)

      - ``broker``: will be set by the strategy in which the sizer is working

        Gives access to information some complex sizers may need like portfolio
        value, ..
    '''
    params = (('stake', 0.9),) # retain 1 - stake for stafety 

    cdef unordered_map[cpp_string, double] getsizing(
        self, unordered_map[cpp_string, double] topk_info,  
        object snapshot, 
        bint isbuy) except *:
        return self._getsizing(topk_info, snapshot, isbuy)

    cpdef unordered_map[cpp_string, double] _getsizing(
        self, 
        unordered_map[cpp_string, double] topk_info, 
        object snapshot, 
        bint isbuy) except *:
        '''This method has to be overriden by subclasses of Sizer to provide
        the sizing functionality

        Params:
          - ``topk_info``: target of the operation, additional information about the target securities 

          - ``snapshot``: a snapshot of the current state of the strategy (see Strategy.snapshot())
          
          - ``isbuy``: will be ``True`` for *buy* operations and ``False``
            for *sell* operations

        The method has to return the actual size (an int) to be executed. If
        ``0`` is returned nothing will be executed.

        The absolute value of the returned value will be used

        '''
        raise NotImplementedError

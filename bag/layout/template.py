# -*- coding: utf-8 -*-
########################################################################################################################
#
# Copyright (c) 2014, Regents of the University of California
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
# following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following
#   disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the
#    following disclaimer in the documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
########################################################################################################################


"""This module defines layout template classes.
"""
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
# noinspection PyUnresolvedReferences,PyCompatibility
from builtins import *

import os
import time
import abc
import copy
from collections import OrderedDict
from itertools import chain
from typing import Union, Dict, Any, List, Set, Type, Optional, Tuple, Generator, TypeVar
import yaml

from bag.core import BagProject
from bag.util.libimport import ClassImporter
from bag.util.interval import IntervalSet
from .core import BagLayout
from .util import BBox, BBoxArray
from ..io import fix_string, get_encoding, open_file
from .routing import Port, TrackID, WireArray, RoutingGrid, UsedTracks
from .objects import Instance, Rect, Via, Path
from future.utils import with_metaclass

# try to import cybagoa module
try:
    import cybagoa
except ImportError:
    cybagoa = None

Layer = Union[str, Tuple[str, str]]

TempBase = TypeVar('TempBase', bound='TemplateBase')


class TemplateDB(object):
    """A database of all templates.

    This class is responsible for keeping track of template libraries and
    creating new templates.

    Parameters
    ----------
    lib_defs : str
        path to the template library definition file.
    routing_grid : RoutingGrid
        the default RoutingGrid object.
    lib_name : str
        the cadence library to put all generated templates in.
    name_prefix : str
        the prefix to append to all layout names.
    use_cybagoa : bool
            True to use cybagoa module to accelerate layout.
    pin_purpose : string
        Default pin purpose name.  Defaults to 'pin'.
    make_pin_rect : bool
        True to create pin object in addition to label.  Defaults to True.
    """

    def __init__(self, lib_defs, routing_grid, lib_name, name_prefix='', use_cybagoa=False,
                 pin_purpose='pin', make_pin_rect=True):
        # type: (str, RoutingGrid, str, str, bool, str, bool) -> None
        self._importer = ClassImporter(lib_defs)

        self._grid = routing_grid
        self._lib_name = lib_name
        self._template_lookup = {}  # type: Dict[Any, TempBase]
        self._name_prefix = name_prefix
        self._used_cell_names = set()  # type: Set[str]
        self._use_cybagoa = use_cybagoa and cybagoa is not None
        self._pin_purpose = pin_purpose
        self._make_pin_rect = make_pin_rect

    @property
    def grid(self):
        # type: () -> RoutingGrid
        """Returns the default routing grid instance."""
        return self._grid

    def append_library(self, lib_name, lib_path):
        # type: (str, str) -> None
        """Adds a new library to the library definition file.

        Parameters
        ----------
        lib_name : str
            name of the library.
        lib_path : str
            path to this library.
        """
        self._importer.append_library(lib_name, lib_path)

    def get_library_path(self, lib_name):
        # type: (str) -> Optional[str]
        """Returns the location of the given library.

        Parameters
        ----------
        lib_name : str
            the library name.

        Returns
        -------
        lib_path : Optional[str]
            the location of the library, or None if library not defined.
        """
        return self._importer.get_library_path(lib_name)

    def get_template_class(self, lib_name, temp_name):
        # type: (str, str) -> Type[TempBase]
        """Returns the Python class for the given template.

        Parameters
        ----------
        lib_name : str
            template library name.
        temp_name : str
            template name

        Returns
        -------
        temp_cls : Type[TempBase]
            the corresponding Python class.
        """
        return self._importer.get_class(lib_name, temp_name)

    def new_template(self, lib_name='', temp_name='', params=None, temp_cls=None, debug=False, **kwargs):
        # type: (str, str, Optional[Dict[str, Any]], Optional[Type[TempBase]], bool, **Any) -> TempBase
        """Create a new template.

        Parameters
        ----------
        lib_name : str
            template library name.
        temp_name : str
            template name
        params : Optional[Dict[str, Any]]
            the parameter dictionary.
        temp_cls : Optional[Type[TempBase]]
            the template class to instantiate.
        debug : bool
            True to print debug messages.
        **kwargs
            optional template parameters.

        Returns
        -------
        template : TempBase
            the new template instance.
        """
        if params is None:
            params = {}

        if temp_cls is None:
            temp_cls = self.get_template_class(lib_name, temp_name)

        kwargs['use_cybagoa'] = self._use_cybagoa
        kwargs['pin_purpose'] = self._pin_purpose
        kwargs['make_pin_rect'] = self._make_pin_rect
        master = temp_cls(self, self._lib_name, params, self._used_cell_names, **kwargs)
        key = master.key

        if key in self._template_lookup:
            master = self._template_lookup[key]
            if debug:
                print('layout cached')
        else:
            if debug:
                print('Computing layout')
            start = time.time()
            master.draw_layout()
            master.finalize()
            end = time.time()
            self._template_lookup[key] = master
            self._used_cell_names.add(master.cell_name)
            if debug:
                print('layout computation took %.4g seconds' % (end - start))

        return master

    def instantiate_layout(self, prj, template, top_cell_name=None, debug=False, flatten=False):
        # type: (BagProject, TempBase, Optional[str], bool, bool) -> None
        """Instantiate the layout of the given :class:`~bag.layout.template.TemplateBase`.

        Parameters
        ----------
        prj : BagProject
            the :class:`~bag.BagProject` instance used to create layout.
        template : TempBase
            the :class:`~bag.layout.template.TemplateBase` to instantiate.
        top_cell_name : Optional[str]
            name of the top level cell.  If None, a default name is used.
        debug : bool
            True to print debugging messages
        flatten : bool
            If True, flatten all template layout.
        """
        self.batch_layout(prj, [template], [top_cell_name], debug=debug, flatten=flatten)

    def batch_layout(self, prj, template_list, name_list=None, debug=False, flatten=False):
        # type: (BagProject, List[TempBase], Optional[List[str]], bool, bool) -> None
        """Instantiate all given templates.

        Parameters
        ----------
        prj : BagProject
            the :class:`~bag.BagProject` instance used to create layout.
        template_list : List[TempBase]
            list of templates to instantiate.
        name_list : Optional[List[str]]
            list of template layout names.  If not given, default names will be used.
        debug : bool
            True to print debugging messages
        flatten : bool
            If True, flatten all template layout.
        """
        if name_list is None:
            name_list = [None] * len(template_list)
        else:
            if len(name_list) != len(template_list):
                raise ValueError("Template list and name list length mismatch.")

        # error checking
        for name in name_list:
            if name in self._used_cell_names:
                raise ValueError('top cell name = %s is already used.' % name)

        if debug:
            print('Retrieving layout info')

        # use ordered dict so that children are created before parents.
        layout_dict = OrderedDict()
        start = time.time()
        real_name_list = []
        for temp, top_name in zip(template_list, name_list):
            real_name = self._instantiate_layout_helper(layout_dict, temp, top_name)
            real_name_list.append(real_name)
        end = time.time()

        if flatten:
            layout_list = [layout_dict[name].get_layout_content(name, flatten=True)
                           for name in real_name_list]
        else:
            layout_list = [master.get_layout_content(cell_name) for cell_name, master in layout_dict.items()]

        if debug:
            print('layout retrieval took %.4g seconds' % (end - start))

        if self._use_cybagoa:
            # remove write locks from old layouts
            cell_view_list = [(item[0], 'layout') for item in layout_list]
            prj.release_write_locks(self._lib_name, cell_view_list)

            if debug:
                print('Instantiating layout')
            # create OALayouts
            start = time.time()
            cds_lib_path = os.environ.get('CDS_LIB_PATH', './cds.lib')
            with cybagoa.PyOALayoutLibrary(cds_lib_path, self._lib_name, get_encoding()) as lib:
                lib.add_layer('prBoundary', 235)
                lib.add_purpose('drawing4', 244)
                lib.add_purpose('drawing6', 246)
                lib.add_purpose('drawing7', 247)
                lib.add_purpose('drawing8', 248)
                lib.add_purpose('boundary', 250)
                lib.add_purpose('pin', 251)

                for cell_name, oa_layout in layout_list:
                    lib.create_layout(cell_name, 'layout', oa_layout)
            end = time.time()
            if debug:
                print('layout instantiation took %.4g seconds' % (end - start))
        else:
            if debug:
                print('Instantiating layout')
            via_tech_name = self._grid.tech_info.via_tech_name
            start = time.time()
            prj.instantiate_layout(self._lib_name, 'layout', via_tech_name, layout_list)
            end = time.time()
            if debug:
                print('layout instantiation took %.4g seconds' % (end - start))

    def _instantiate_layout_helper(self, layout_dict, template, top_cell_name):
        # type: (Dict[str, TempBase], TempBase, Optional[str]) -> str
        """Helper method for batch_layout().

        Parameters
        ----------
        layout_dict : Dict[str, TempBase]
            dictionary from template cell name to TemplateBase.
        template : TempBase
            the :class:`~bag.layout.template.Template` to instantiate.
        top_cell_name : Optional[str]
            name of the top level cell.  If None, a default name is used.

        Returns
        -------
        layout_name : str
            the template cell name.
        """
        # get template master for all children
        for template_key in template.children:
            child_temp = self._template_lookup[template_key]
            if child_temp.cell_name not in layout_dict:
                self._instantiate_layout_helper(layout_dict, child_temp, None)

        # get template master for this cell.
        layout_name = top_cell_name or template.cell_name
        layout_dict[layout_name] = self._template_lookup[template.key]

        return layout_name


class TemplateBase(with_metaclass(abc.ABCMeta, object)):
    """The base template class.

    Parameters
    ----------
    temp_db : TemplateDB
            the template database.
    lib_name : str
        the layout library name.
    params : Dict[str, Any]
        the parameter values.
    used_names : Set[str]
        a set of already used cell names.
    **kwargs
        dictionary of the following optional parameters:

        grid : RoutingGrid
            the routing grid to use for this template.
        use_cybagoa : bool
            True to use cybagoa module to accelerate layout.
        pin_purpose : string
            Default pin purpose name.  Defaults to 'pin'.
        make_pin_rect : bool
            True to create pin object in addition to label.  Defaults to True.

    Attributes
    ----------
    pins : dict
        the pins dictionary.
    children : List[str]
        a list of template cells this template uses.
    params : Dict[str, Any]
        the parameter values of this template.
    """

    def __init__(self, temp_db, lib_name, params, used_names, **kwargs):
        # type: (TemplateDB, str, Dict[str, Any], Set[str], **Any) -> None
        # initialize template attributes
        self._grid = kwargs.get('grid', temp_db.grid)
        self._layout = BagLayout(self.grid,
                                 use_cybagoa=kwargs.get('use_cybagoa', False),
                                 pin_purpose=kwargs.get('pin_purpose', 'pin'),
                                 make_pin_rect=kwargs.get('make_pin_rect', True))
        self._temp_db = temp_db
        self._size = None  # type: Tuple[int, int, int]
        self.pins = {}
        self.children = None
        self._lib_name = lib_name
        self._ports = {}
        self._port_params = {}
        self._array_box = None  # type: BBox
        self._finalized = False
        self._used_tracks = UsedTracks()

        # set parameters
        self.params = {}
        default_params = self.get_default_param_values()
        # check all parameters are set
        for key, desc in self.get_params_info().items():
            if key not in params:
                if key not in default_params:
                    raise ValueError('Parameter %s not specified.  Description:\n%s' % (key, desc))
                else:
                    self.params[key] = default_params[key]
            else:
                self.params[key] = params[key]

        # get unique cell name
        self._cell_name = self._get_unique_cell_name(used_names)

        self._key = self.compute_unique_key()

    def new_template_with(self, **kwargs):
        # type: (TempBase, **Any) -> TempBase
        """Create a new template with the given parameters.

        This method will update the parameter values with the given dictionary,
        then create a new template with those parameters and return it.

        Parameters
        ----------
        **kwargs
            a dictionary of new parameter values.
        """
        # get new parameter dictionary.
        new_params = copy.deepcopy(self.params)
        for key, val in kwargs.items():
            if key in new_params:
                new_params[key] = val

        return self._temp_db.new_template(params=new_params, temp_cls=self.__class__,
                                          grid=self.grid)

    @property
    def template_db(self):
        # type: () -> TemplateDB
        """Returns the template database object"""
        return self._temp_db

    @property
    def grid(self):
        # type: () -> RoutingGrid
        """Returns the RoutingGrid object"""
        return self._grid

    @grid.setter
    def grid(self, new_grid):
        # type: (RoutingGrid) -> None
        """Change the RoutingGrid of this template."""
        if not self._finalized:
            self._grid = new_grid
        else:
            raise RuntimeError('Template already finalized.')

    @property
    def array_box(self):
        # type: () -> BBox
        """Returns the array/abutment bounding box of this template."""
        return self._array_box

    @array_box.setter
    def array_box(self, new_array_box):
        # type: (BBox) -> None
        """Sets the array/abutment bound box of this template."""
        if not self._finalized:
            self._array_box = new_array_box
        else:
            raise RuntimeError('Template already finalized.')

    @property
    def size(self):
        # type: () -> Tuple[int, int, int]
        """The size of this template, in (layer, num_x_block,  num_y_block) format."""
        return self._size

    @property
    def bound_box(self):
        # type: () -> Optional[BBox]
        """Returns the BBox with the size of this template.  None if size not set yet."""
        mysize = self.size
        if mysize is None:
            return None
        wblk_unit, hblk_unit = self.grid.get_block_size(mysize[0], unit_mode=True)
        return BBox(0, 0, wblk_unit * mysize[1], hblk_unit * mysize[2], self.grid.resolution,
                    unit_mode=True)

    @size.setter
    def size(self, new_size):
        # type: (Tuple[int, int, int]) -> None
        """Sets the size of this template."""
        if not self._finalized:
            self._size = new_size
        else:
            raise RuntimeError('Template already finalized.')

    @property
    def lib_name(self):
        # type: () -> str
        """The layout library name"""
        return self._lib_name

    @property
    def cell_name(self):
        # type: () -> str
        """The layout cell name"""
        return self._cell_name

    @property
    def key(self):
        # type: () -> Any
        """A unique key representing this template."""
        return self._key

    def set_size_from_array_box(self, top_layer_id):
        # type: (int) -> None
        """Automatically compute the size from array_box.

        Assumes the array box is exactly in the center of the template.

        Parameters
        ----------
        top_layer_id : int
            the top level routing layer ID that array box is calculated with.
        """
        h_pitch = self.grid.get_block_pitch(top_layer_id, unit_mode=True)
        w_pitch = self.grid.get_block_pitch(top_layer_id - 1, unit_mode=True)
        if self.grid.get_direction(top_layer_id) == 'y':
            h_pitch, w_pitch = w_pitch, h_pitch

        dx = self.array_box.left_unit
        dy = self.array_box.bottom_unit
        if dx < 0 or dy < 0:
            raise ValueError('lower-left corner of array box must be in first quadrant.')

        w_blk = 2 * dx + self.array_box.width_unit
        h_blk = 2 * dy + self.array_box.height_unit

        wq, wr = divmod(w_blk, w_pitch)
        hq, hr = divmod(h_blk, h_pitch)
        if wr != 0:
            raise ValueError('block width = %d not in block pitch (%d)' % (w_blk, w_pitch))
        if hr != 0:
            raise ValueError('block height = %d not in block pitch (%d)' % (h_blk, h_pitch))

        self.size = top_layer_id, wq, hq

    @classmethod
    def to_immutable_id(cls, val):
        # type: (Any) -> Any
        """Convert the given object to an immutable type for use as keys in dictionary.
        """
        # python 2/3 compatibility: convert raw bytes to string
        val = fix_string(val)

        if val is None or isinstance(val, int) or isinstance(val, str) or isinstance(val, float):
            return val
        elif isinstance(val, list) or isinstance(val, tuple):
            return tuple((cls.to_immutable_id(item) for item in val))
        elif isinstance(val, dict):
            return tuple(((k, cls.to_immutable_id(val[k])) for k in sorted(val.keys())))
        else:
            raise Exception('Unrecognized value %s with type %s' % (str(val), type(val)))

    @classmethod
    @abc.abstractmethod
    def get_params_info(cls):
        # type: () -> Dict[str, str]
        """Returns a dictionary containing parameter descriptions.

        Override this method to return a dictionary from parameter names to descriptions.

        Returns
        -------
        param_info : Dict[str, str]
            dictionary from parameter name to description.
        """
        return {}

    @classmethod
    def get_default_param_values(cls):
        # type: () -> Dict[str, Any]
        """Returns a dictionary containing default parameter values.

        Override this method to define default parameter values.  As good practice,
        you should avoid defining default values for technology-dependent parameters
        (such as channel length, transistor width, etc.), but only define default
        values for technology-independent parameters (such as number of tracks).

        Returns
        -------
        default_params : Dict[str, Any]
            dictionary of default parameter values.
        """
        return {}

    @classmethod
    def is_micro(cls):
        # type: () -> bool
        """Returns True if this template is a micro template.

        Override this method to return True if this is a micro template.

        Returns
        -------
        is_primitive : bool
            True if this template is a primitive template.
        """
        return False

    @abc.abstractmethod
    def draw_layout(self):
        # type: () -> None
        """Draw the layout of this template.

        Override this method to create the layout.

        WARNING: you should never call this method yourself.
        """
        pass

    def finalize(self):
        # type: () -> None
        """Prevents any further changes to this template."""
        # construct port objects
        for net_name, port_params in self._port_params.items():
            pin_dict = port_params['pins']
            if port_params['show']:
                label = port_params['label']
                for wire_arr_list in pin_dict.values():
                    for wire_arr in wire_arr_list:  # type: WireArray
                        for layer_name, bbox in wire_arr.wire_iter(self.grid):
                            self._layout.add_pin(net_name, layer_name, bbox, label=label)
            self._ports[net_name] = Port(net_name, pin_dict)

        # finalize layout
        self._layout.finalize()
        # get set of children keys
        self.children = self._layout.get_masters_set()
        self._finalized = True

    def write_summary_file(self, fname, lib_name, cell_name):
        """Create a summary file for this template layout."""
        # get all pin information
        pin_dict = {}
        for port_name in self.port_names_iter():
            pin_cnt = 0
            port = self.get_port(port_name)
            for pin_warr in port:
                for layer_name, bbox in pin_warr.wire_iter(self.grid):
                    if pin_cnt == 0:
                        pin_name = port_name
                    else:
                        pin_name = '%s_%d' % (port_name, pin_cnt)
                    pin_cnt += 1
                    pin_dict[pin_name] = dict(
                        layer=[layer_name, 'pin'],
                        netname=port_name,
                        xy0=[bbox.left, bbox.bottom],
                        xy1=[bbox.right, bbox.top],
                    )

        # get size information
        my_width, my_height = self.grid.get_size_dimension(self.size)
        info = {
            lib_name: {
                cell_name: dict(
                    pins=pin_dict,
                    xy0=[0.0, 0.0],
                    xy1=[my_width, my_height],
                ),
            },
        }

        with open_file(fname, 'w') as f:
            yaml.dump(info, f)

    def get_flat_geometries(self):
        # type: () -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]
        """Returns flattened geometries in this template."""
        return self._layout.get_flat_geometries()

    def get_layout_content(self, cell_name, flatten=False):
        # type: (str, bool) -> Union[List[Any], 'cybagoa.PyOALayout']
        """Returns the layout content of this template.

        Parameters
        ----------
        cell_name : str
            the layout top level cell name.
        flatten : bool
            True to flatten all children

        Returns
        -------
        content : Union[List[Any], 'cybagoa.PyOALayout']
            a list describing this layout, or PyOALayout if cybagoa is enabled.
        """
        return self._layout.get_content(cell_name, flatten=flatten)

    def _get_unique_cell_name(self, used_names):
        # type: (Set[str]) -> str
        """Returns a unique cell name.

        Parameters
        ----------
        used_names : Set[str]
            a set of used names.

        Returns
        -------
        cell_name : str
            the unique cell name.
        """
        counter = 0
        basename = self.get_layout_basename()
        cell_name = basename
        while cell_name in used_names:
            counter += 1
            cell_name = '%s_%d' % (basename, counter)

        return cell_name

    def _get_qualified_name(self):
        # type: () -> str
        """Returns the qualified name of this class."""
        module = self.__class__.__module__
        if module is None or module == str.__class__.__module__:
            return self.__class__.__name__
        else:
            return module + '.' + self.__class__.__name__

    def compute_unique_key(self):
        # type: () -> Any
        """Returns a unique hashable object (usually tuple or string) that represents the given parameters.

        Returns
        -------
        unique_id : Any
            a hashable unique ID representing the given parameters.
        """

        return self.to_immutable_id((self._get_qualified_name(), self.params))

    def get_layout_basename(self):
        # type: () -> str
        """Returns the base name for this template.

        Returns
        -------
        base_name : str
            the base name of this template.
        """
        return self.__class__.__name__

    def get_pin_name(self, name):
        # type: (str) -> str
        """Get the actual name of the given pin from the renaming dictionary.

        Given a pin name, If this Template has a parameter called 'rename_dict',
        return the actual pin name from the renaming dictionary.

        Parameters
        ----------
        name : str
            the pin name.

        Returns
        -------
        actual_name : str
            the renamed pin name.
        """
        rename_dict = self.params.get('rename_dict', {})
        return rename_dict.get(name, name)

    def get_port(self, name=''):
        # type: (str) -> Port
        """Returns the port object with the given name.

        Parameters
        ----------
        name : str
            the port terminal name.  If None or empty, check if this template has only one port, then return it.

        Returns
        -------
        port : Port
            the port object.
        """
        if not name:
            if len(self._ports) != 1:
                raise ValueError('Template has %d ports != 1.' % len(self._ports))
            name = next(iter(self._ports))
        return self._ports[name]

    def has_port(self, port_name):
        # type: (str) -> bool
        """Returns True if this template has the given port."""
        return port_name in self._ports

    def port_names_iter(self):
        # type: () -> Generator[str]
        """Iterates over port names in this template.

        Yields
        ------
        port_name : string
            name of a port in this template.
        """
        return self._ports.keys()

    def new_template(self, lib_name='', temp_name='', params=None, temp_cls=None, debug=False, **kwargs):
        # type: (str, str, Optional[Dict[str, Any]], Optional[Type[TempBase]], bool, **Any) -> TempBase
        """Create a new template.

        Parameters
        ----------
        lib_name : str
            template library name.
        temp_name : str
            template name
        params : Optional[Dict[str, Any]]
            the parameter dictionary.
        temp_cls : Optional[Type[TempBase]]
            the template class to instantiate.
        debug : bool
            True to print debug messages.
        **kwargs
            optional template parameters.

        Returns
        -------
        template : TempBase
            the new template instance.
        """
        if 'grid' not in kwargs:
            kwargs['grid'] = self.grid
        return self._temp_db.new_template(lib_name=lib_name, temp_name=temp_name,
                                          params=params,
                                          temp_cls=temp_cls,
                                          debug=debug,
                                          **kwargs)

    def move_all_by(self, dx=0.0, dy=0.0):
        # type: (float, float) -> None
        """Move all layout objects Except pins in this layout by the given amount.

        primitive pins will be moved, but pins on routing grid will not.

        Parameters
        ----------
        dx : float
            the X shift.
        dy : float
            the Y shift.
        """
        self._layout.move_all_by(dx=dx, dy=dy)

    def add_instance(self, master, inst_name=None, loc=(0.0, 0.0),
                     orient="R0", nx=1, ny=1, spx=0.0, spy=0.0, unit_mode=False):
        # type: (TempBase, Optional[str], Tuple[float, float], str, int, int, float, float) -> Instance
        """Adds a new (arrayed) instance to layout.

        Parameters
        ----------
        master : TempBase
            the master template object.
        inst_name : Optional[str]
            instance name.  If None or an instance with this name already exists,
            a generated unique name is used.
        loc : Tuple[float, float]
            instance location.
        orient : str
            instance orientation.  Defaults to "R0"
        nx : int
            number of columns.  Must be positive integer.
        ny : int
            number of rows.  Must be positive integer.
        spx : float
            column pitch.  Used for arraying given instance.
        spy : float
            row pitch.  Used for arraying given instance.
        unit_mode : bool
            True if dimensions are given in resolution units.

        Returns
        -------
        inst : Instance
            the added instance.
        """
        inst = Instance(self._lib_name, master, loc=loc, orient=orient,
                        res=self.grid.resolution, name=inst_name,
                        nx=nx, ny=ny, spx=spx, spy=spy, unit_mode=unit_mode)

        self._layout.add_instance(inst)
        return inst

    def add_instance_primitive(self, lib_name,  # type: str
                               cell_name,  # type: str
                               loc,  # type: Tuple[float, float]
                               view_name='layout',  # type: str
                               inst_name=None,  # type: Optional[str]
                               orient="R0",  # type: str
                               nx=1,  # type: int
                               ny=1,  # type: int
                               spx=0.0,  # type: float
                               spy=0.0,  # type: float
                               params=None,  # type: Optional[Dict[str, Any]]
                               **kwargs  # type: Any
                               ):
        # type: (...) -> None
        """Adds a new (arrayed) primitive instance to layout.

        Parameters
        ----------
        lib_name : str
            instance library name.
        cell_name : str
            instance cell name.
        loc : Tuple[float, float]
            instance location.
        view_name : str
            instance view name.  Defaults to 'layout'.
        inst_name : Optional[str]
            instance name.  If None or an instance with this name already exists,
            a generated unique name is used.
        orient : str
            instance orientation.  Defaults to "R0"
        nx : int
            number of columns.  Must be positive integer.
        ny : int
            number of rows.  Must be positive integer.
        spx : float
            column pitch.  Used for arraying given instance.
        spy : float
            row pitch.  Used for arraying given instance.
        params : Optional[Dict[str, Any]]
            the parameter dictionary.  Used for adding pcell instance.
        **kwargs
            additional arguments.  Usually implementation specific.
        """
        self._layout.add_instance_primitive(lib_name, cell_name, loc,
                                            view_name=view_name, inst_name=inst_name,
                                            orient=orient, num_rows=ny, num_cols=nx,
                                            sp_rows=spy, sp_cols=spx,
                                            params=params, **kwargs)

    def add_rect(self, layer, bbox, nx=1, ny=1, spx=0.0, spy=0.0):
        # type: (Layer, Union[BBox, BBoxArray], int, int, float, float) -> Rect
        """Add a new (arrayed) rectangle.

        Parameters
        ----------
        layer: Layer
            the layer name, or the (layer, purpose) pair.
        bbox : Union[BBox, BBoxArray]
            the rectangle bounding box.  If BBoxArray is given, its arraying parameters will be used instead.
        nx : int
            number of columns.
        ny : int
            number of rows.
        spx : float
            column pitch.
        spy : float
            row pitch.

        Returns
        -------
        rect : Rect
            the added rectangle.
        """
        if isinstance(bbox, BBoxArray):
            nx, ny, spx, spy = bbox.nx, bbox.ny, bbox.spx, bbox.spy
            bbox = bbox.base
        else:
            pass

        rect = Rect(layer, bbox, nx=nx, ny=ny, spx=spx, spy=spy)
        self._layout.add_rect(rect)
        return rect

    def add_path(self, path):
        """Add a new path.

        Parameters
        ----------
        path : Path
            the path to add.
        """
        self._layout.add_path(path)
        return path

    def reexport(self, port, net_name='', label='', show=True,
                 fill_margin=0, fill_type='VSS', unit_mode=False):
        # type: (Port, str, str, bool, Union[float, int], str, bool) -> None
        """Re-export the given port object.

        Add all geometries in the given port as pins with optional new name
        and label.

        Parameters
        ----------
        port : Port
            the Port object to re-export.
        net_name : str
            the new net name.  If not given, use the port's current net name.
        label : str
            the label.  If not given, use net_name.
        show : bool
            True to draw the pin in layout.
        fill_margin : Union[float, int]
            minimum margin between wires and fill.
        fill_type : str
            fill connection type.  Either 'VDD' or 'VSS'.  Defaults to 'VSS'.
        unit_mode : bool
            True if fill_margin is given in resolution units.
        """
        net_name = net_name or port.net_name
        label = label or net_name

        if net_name not in self._port_params:
            self._port_params[net_name] = dict(label=label, pins={}, show=show)

        port_params = self._port_params[net_name]
        # check labels is consistent.
        if port_params['label'] != label:
            msg = 'Current port label = %s != specified label = %s'
            raise ValueError(msg % (port_params['label'], label))
        if port_params['show'] != show:
            raise ValueError('Conflicting show port specification.')

        # export all port geometries
        port_pins = port_params['pins']
        for wire_arr in port:
            self._used_tracks.add_wire_arrays(self.grid, wire_arr, fill_margin=fill_margin, fill_type=fill_type,
                                              unit_mode=unit_mode)
            layer_id = wire_arr.layer_id
            if layer_id not in port_pins:
                port_pins[layer_id] = [wire_arr]
            else:
                port_pins[layer_id].append(wire_arr)

    def add_pin_primitive(self, net_name, layer, bbox, label=''):
        # type: (str, str, BBox, str) -> None
        """Add a primitive pin to the layout.

        A primitive pin will not show up as a port.  This is mainly used to add necessary
        label/pin for LVS purposes.

        Parameters
        ----------
        net_name : str
            the net name associated with the pin.
        layer : str
            the pin layer name.
        bbox : BBox
            the pin bounding box.
        label : str
            the label of this pin.  If None or empty, defaults to be the net_name.
            this argument is used if you need the label to be different than net name
            for LVS purposes.  For example, unconnected pins usually need a colon after
            the name to indicate that LVS should short those pins together.
        """
        self._layout.add_pin(net_name, layer, bbox, label=label)

    def add_pin(self, net_name, wire_arr_list, label='', show=True):
        # type: (str, Union[WireArray, List[WireArray]], str, bool) -> None
        """Add new pin to the layout.

        If one or more pins with the same net name already exists,
        they'll be grouped under the same port.

        Parameters
        ----------
        net_name : str
            the net name associated with the pin.
        wire_arr_list : Union[WireArray, List[WireArray]]
            WireArrays representing the pin geometry.
        label : str
            the label of this pin.  If None or empty, defaults to be the net_name.
            this argument is used if you need the label to be different than net name
            for LVS purposes.  For example, unconnected pins usually need a colon after
            the name to indicate that LVS should short those pins together.
        show : bool
            if True, draw the pin in layout.
        """
        if isinstance(wire_arr_list, WireArray):
            wire_arr_list = [wire_arr_list]
        else:
            pass

        label = label or net_name

        if net_name not in self._port_params:
            self._port_params[net_name] = dict(label=label, pins={}, show=show)

        port_params = self._port_params[net_name]

        # check labels is consistent.
        if port_params['label'] != label:
            msg = 'Current port label = %s != specified label = %s'
            raise ValueError(msg % (port_params['label'], label))
        if port_params['show'] != show:
            raise ValueError('Conflicting show port specification.')

        for wire_arr in wire_arr_list:
            # add pin array to port_pins
            layer_id = wire_arr.track_id.layer_id
            port_pins = port_params['pins']
            if layer_id not in port_pins:
                port_pins[layer_id] = [wire_arr]
            else:
                port_pins[layer_id].append(wire_arr)

    def add_via(self, bbox, bot_layer, top_layer, bot_dir,
                nx=1, ny=1, spx=0.0, spy=0.0):
        # type: (BBox, Layer, Layer, str, int, int, float, float) -> Via
        """Adds a (arrayed) via object to the layout.

        Parameters
        ----------
        bbox : BBox
            the via bounding box, not including extensions.
        bot_layer : Layer
            the bottom layer name, or a tuple of layer name and purpose name.
            If purpose name not given, defaults to 'drawing'.
        top_layer : Layer
            the top layer name, or a tuple of layer name and purpose name.
            If purpose name not given, defaults to 'drawing'.
        bot_dir : str
            the bottom layer extension direction.  Either 'x' or 'y'.
        nx : int
            number of columns.
        ny : int
            number of rows.
        spx : float
            column pitch.
        spy : float
            row pitch.

        Returns
        -------
        via : Via
            the created via object.
        """
        via = Via(self.grid.tech_info, bbox, bot_layer, top_layer, bot_dir,
                  nx=nx, ny=ny, spx=spx, spy=spy)
        self._layout.add_via(via)

        return via

    def add_via_primitive(self, via_type,  # type: str
                          loc,  # type: List[float]
                          num_rows=1,  # type: int
                          num_cols=1,  # type: int
                          sp_rows=0.0,  # type: float
                          sp_cols=0.0,  # type: float
                          enc1=None,  # type: Optional[List[float]]
                          enc2=None,  # type: Optional[List[float]]
                          orient='R0',  # type: str
                          cut_width=None,  # type: Optional[float]
                          cut_height=None,  # type: Optional[float]
                          nx=1,  # type: int
                          ny=1,  # type: int
                          spx=0.0,  # type: float
                          spy=0.0  # type: float
                          ):
        # type: (...) -> None
        """Adds a via by specifying all parameters.

        Parameters
        ----------
        via_type : str
            the via type name.
        loc : List[float]
            the via location as a two-element list.
        num_rows : int
            number of via cut rows.
        num_cols : int
            number of via cut columns.
        sp_rows : float
            spacing between via cut rows.
        sp_cols : float
            spacing between via cut columns.
        enc1 : Optional[List[float]]
            a list of left, right, top, and bottom enclosure values on bottom layer.  Defaults to all 0.
        enc2 : Optional[List[float]]
            a list of left, right, top, and bottom enclosure values on top layer.  Defaults. to all 0.
        orient : str
            orientation of the via.
        cut_width : Optional[float]
            via cut width.  This is used to create rectangle via.
        cut_height : Optional[float]
            via cut height.  This is used to create rectangle via.
        nx : int
            number of columns.
        ny : int
            number of rows.
        spx : float
            column pitch.
        spy : float
            row pitch.
        """
        self._layout.add_via_primitive(via_type, loc, num_rows=num_rows, num_cols=num_cols,
                                       sp_rows=sp_rows, sp_cols=sp_cols,
                                       enc1=enc1, enc2=enc2, orient=orient,
                                       cut_width=cut_width, cut_height=cut_height,
                                       arr_nx=nx, arr_ny=ny, arr_spx=spx, arr_spy=spy)

    def connect_wires(self,  # type: TemplateBase
                      wire_arr_list,  # type: Union[WireArray, List[WireArray]]
                      lower=None,  # type: Optional[Union[int, float]]
                      upper=None,  # type: Optional[Union[int, float]]
                      debug=False,  # type: bool
                      fill_margin=0,  # type: Union[int, float]
                      fill_type='VSS',  # type: str
                      unit_mode=False  # type: bool
                      ):
        # type: (...) -> List[WireArray]
        """Connect all given WireArrays together.

        all WireArrays must be on the same layer.

        Parameters
        ----------
        wire_arr_list : Union[WireArr, List[WireArr]]
            WireArrays to connect together.
        lower : Optional[Union[int, float]]
            if given, extend connection wires to this lower coordinate.
        upper : Optional[Union[int, float]]
            if given, extend connection wires to this upper coordinate.
        debug : bool
            True to print debug messages.
        fill_margin : Union[float, int]
            minimum margin between wires and fill.
        fill_type : str
            fill connection type.  Either 'VDD' or 'VSS'.  Defaults to 'VSS'.
        unit_mode: bool
            True if lower/upper/fill_margin is given in resolution units.

        Returns
        -------
        conn_list : List[WireArray]
            list of connection wires created.
        """
        grid = self.grid
        res = grid.resolution

        if not unit_mode:
            fill_margin = int(round(fill_margin / res))
            if lower is not None:
                lower = int(round(lower / res))
            if upper is not None:
                upper = int(round(upper / res))

        if isinstance(wire_arr_list, WireArray):
            wire_arr_list = [wire_arr_list]
        else:
            pass

        if not wire_arr_list:
            # do nothing
            return []

        # calculate wire vertical coordinates
        a = wire_arr_list[0]
        layer_id = a.layer_id
        direction = grid.get_direction(layer_id)
        perp_dir = 'y' if direction == 'x' else 'x'
        track_pitch = grid.get_track_pitch(layer_id, unit_mode=True)
        intv_set = IntervalSet()

        for wire_arr in wire_arr_list:
            if wire_arr.layer_id != layer_id:
                raise ValueError('WireArray layer ID != %d' % layer_id)

            cur_range = (int(round(wire_arr.lower / res)),
                         int(round(wire_arr.upper / res)))
            if lower is not None:
                cur_range = (min(cur_range[0], lower), max(cur_range[1], lower))
            if upper is not None:
                cur_range = (min(cur_range[0], upper), max(cur_range[1], upper))

            box_arr = wire_arr.get_bbox_array(grid)
            for box in box_arr:
                intv = box.get_interval(perp_dir, unit_mode=True)
                try:
                    old_range = intv_set[intv]
                    intv_set[intv] = min(cur_range[0], old_range[0]), max(cur_range[1], old_range[1])
                except KeyError:
                    success = intv_set.add(intv, cur_range)
                    if not success:
                        raise ValueError('wire interval {} overlap existing wires.'.format(intv))

        # draw wires, group into arrays
        new_warr_list = []
        base_range = None
        base_intv = None
        base_width = None
        count = 0
        pitch = 0
        last_lower = 0
        for intv, wrange in intv_set.items():
            if debug:
                print('wires intv: %s, range: %s' % (intv, wrange))
            cur_width = intv[1] - intv[0]
            cur_lower = intv[0]
            if count == 0:
                base_range = wrange
                base_intv = intv
                base_width = intv[1] - intv[0]
                count = 1
                pitch = 0
            else:
                if wrange[0] == base_range[0] and \
                                wrange[1] == base_range[1] and \
                                base_width == cur_width:
                    # length and width matches
                    cur_pitch = cur_lower - last_lower
                    if count == 1:
                        # second wire, set pitch
                        pitch = cur_pitch // track_pitch
                        count += 1
                    elif pitch == cur_pitch:
                        # pitch matches
                        count += 1
                    else:
                        # pitch does not match, add current wires and start anew
                        tr_idx, tr_width = self.grid.interval_to_track(layer_id, base_intv, unit_mode=True)
                        track_id = TrackID(layer_id, tr_idx, tr_width, num=count, pitch=pitch)
                        warr = WireArray(track_id, base_range[0] * res, base_range[1] * res)
                        for layer_name, bbox_arr in warr.wire_arr_iter(self.grid):
                            self.add_rect(layer_name, bbox_arr)
                        new_warr_list.append(warr)
                        base_range = wrange
                        base_intv = intv
                        base_width = cur_width
                        count = 1
                        pitch = 0.0
                else:
                    # length/width does not match, add cumulated wires and start anew
                    tr_idx, tr_width = self.grid.interval_to_track(layer_id, base_intv, unit_mode=True)
                    track_id = TrackID(layer_id, tr_idx, tr_width, num=count, pitch=pitch)
                    warr = WireArray(track_id, base_range[0] * res, base_range[1] * res)
                    for layer_name, bbox_arr in warr.wire_arr_iter(self.grid):
                        self.add_rect(layer_name, bbox_arr)
                    new_warr_list.append(warr)
                    base_range = wrange
                    base_intv = intv
                    base_width = cur_width
                    count = 1
                    pitch = 0.0

            # update last lower coordinate
            last_lower = cur_lower

        # add last wires
        tr_idx, tr_width = self.grid.interval_to_track(layer_id, base_intv, unit_mode=True)
        track_id = TrackID(layer_id, tr_idx, tr_width, num=count, pitch=pitch)
        warr = WireArray(track_id, base_range[0] * res, base_range[1] * res)
        for layer_name, bbox_arr in warr.wire_arr_iter(self.grid):
            self.add_rect(layer_name, bbox_arr)
        new_warr_list.append(warr)

        self._used_tracks.add_wire_arrays(grid, new_warr_list, fill_margin=fill_margin, fill_type=fill_type,
                                          unit_mode=True)
        return new_warr_list

    def _draw_via_on_track(self, wlayer, box_arr, track_id, tl_unit=None,
                           tu_unit=None):
        # type: (str, BBoxArray, TrackID, float, float) -> Tuple[float, float]
        """Helper method.  Draw vias on the intersection of the BBoxArray and TrackID."""
        grid = self.grid
        res = grid.resolution

        tr_layer_id = track_id.layer_id
        tr_width = track_id.width
        tr_dir = grid.get_direction(tr_layer_id)
        tr_pitch = grid.get_track_pitch(tr_layer_id)

        w_layer_id = grid.tech_info.get_layer_id(wlayer)
        w_dir = 'x' if tr_dir == 'y' else 'y'
        wbase = box_arr.base
        for sub_track_id in track_id.sub_tracks_iter(grid):
            base_idx = sub_track_id.base_index
            if w_layer_id > tr_layer_id:
                bot_layer = grid.get_layer_name(tr_layer_id, base_idx)
                top_layer = wlayer
                bot_dir = tr_dir
            else:
                bot_layer = wlayer
                top_layer = grid.get_layer_name(tr_layer_id, base_idx)
                bot_dir = w_dir
            # compute via bounding box
            tl, tu = grid.get_wire_bounds(tr_layer_id, base_idx, width=tr_width, unit_mode=True)
            if tr_dir == 'x':
                via_box = BBox(wbase.left_unit, tl, wbase.right_unit, tu, res, unit_mode=True)
                nx, ny = box_arr.nx, sub_track_id.num
                spx, spy = box_arr.spx, sub_track_id.pitch * tr_pitch
                via = self.add_via(via_box, bot_layer, top_layer, bot_dir,
                                   nx=nx, ny=ny, spx=spx, spy=spy)
                vtbox = via.bottom_box if w_layer_id > tr_layer_id else via.top_box
                if tl_unit is None:
                    tl_unit = vtbox.left_unit
                else:
                    tl_unit = min(tl_unit, vtbox.left_unit)
                if tu_unit is None:
                    tu_unit = vtbox.right_unit + (nx - 1) * box_arr.spx_unit
                else:
                    tu_unit = max(tu_unit, vtbox.right_unit + (nx - 1) * box_arr.spx_unit)
            else:
                via_box = BBox(tl, wbase.bottom_unit, tu, wbase.top_unit, res, unit_mode=True)
                nx, ny = sub_track_id.num, box_arr.ny
                spx, spy = sub_track_id.pitch * tr_pitch, box_arr.spy
                via = self.add_via(via_box, bot_layer, top_layer, bot_dir,
                                   nx=nx, ny=ny, spx=spx, spy=spy)
                vtbox = via.bottom_box if w_layer_id > tr_layer_id else via.top_box
                if tl_unit is None:
                    tl_unit = vtbox.bottom_unit
                else:
                    tl_unit = min(tl_unit, vtbox.bottom_unit)
                if tu_unit is None:
                    tu_unit = vtbox.top_unit + (ny - 1) * box_arr.spy_unit
                else:
                    tu_unit = max(tu_unit, vtbox.top_unit + (ny - 1) * box_arr.spy_unit)

        return tl_unit, tu_unit

    def connect_bbox_to_tracks(self,  # type: TemplateBase
                               layer_name,  # type: str
                               box_arr,  # type: Union[BBox, BBoxArray]
                               track_id,  # type: TrackID
                               track_lower=None,  # type: Optional[Union[int, float]]
                               track_upper=None,  # type: Optional[Union[int, float]]
                               fill_margin=0,  # type: Union[int, float]
                               fill_type='VSS',  # type: str
                               unit_mode=False  # type: bool
                               ):
        # type: (...) -> WireArray
        """Connect the given lower layer to given tracks.

        This method is used to connect layer below RoutingGrid to RoutingGrid.

        Parameters
        ----------
        layer_name : str
            the lower level layer name.
        box_arr : Union[BBox, BBoxArray]
            bounding box of the wire(s) to connect to tracks.
        track_id : TrackID
            TrackID that specifies the track(s) to connect the given wires to.
        track_lower : Optional[Union[int, float]]
            if given, extend track(s) to this lower coordinate.
        track_upper : Optional[Union[int, float]]
            if given, extend track(s) to this upper coordinate.
        fill_margin : Union[int, float]
            minimum margin between wires and fill.
        fill_type : str
            fill connection type.  Either 'VDD' or 'VSS'.  Defaults to 'VSS'.
        unit_mode: bool
            True if track_lower/track_upper/fill_margin is given in resolution units.

        Returns
        -------
        wire_arr : WireArray
            WireArray representing the tracks created.
        """
        if isinstance(box_arr, BBox):
            box_arr = BBoxArray(box_arr)
        else:
            pass

        res = self.grid.resolution

        # extend bounding boxes to tracks
        tl, tu = track_id.get_bounds(self.grid, unit_mode=True)
        tr_dir = self.grid.get_direction(track_id.layer_id)
        base = box_arr.base
        if tr_dir == 'x':
            self.add_rect(layer_name, base.extend(y=tl, unit_mode=True).extend(y=tu, unit_mode=True),
                          nx=box_arr.nx, ny=box_arr.ny, spx=box_arr.spx, spy=box_arr.spy)
        else:
            self.add_rect(layer_name, base.extend(x=tl, unit_mode=True).extend(x=tu, unit_mode=True),
                          nx=box_arr.nx, ny=box_arr.ny, spx=box_arr.spx, spy=box_arr.spy)

        # draw vias
        tl_unit = track_lower
        tu_unit = track_upper
        if not unit_mode:
            fill_margin = int(round(fill_margin / res))
            if track_lower is not None:
                tl_unit = int(round(track_lower / res))
            if track_upper is not None:
                tu_unit = int(round(track_upper / res))

        tl_unit, tu_unit = self._draw_via_on_track(layer_name, box_arr, track_id,
                                                   tl_unit=tl_unit, tu_unit=tu_unit)

        # draw tracks
        result = WireArray(track_id, tl_unit * res, tu_unit * res)
        for layer_name, bbox_arr in result.wire_arr_iter(self.grid):
            self.add_rect(layer_name, bbox_arr)

        self._used_tracks.add_wire_arrays(self.grid, result, fill_margin=fill_margin, fill_type=fill_type,
                                          unit_mode=True)
        return result

    def connect_to_tracks(self,  # type: TemplateBase
                          wire_arr_list,  # type: Union[WireArray, List[WireArray]]
                          track_id,  # type: TrackID
                          wire_lower=None,  # type: Optional[Union[float, int]]
                          wire_upper=None,  # type: Optional[Union[float, int]]
                          track_lower=None,  # type: Optional[Union[float, int]]
                          track_upper=None,  # type: Optional[Union[float, int]]
                          fill_margin=0,  # type: Union[int, float]
                          fill_type='VSS',  # type: str
                          unit_mode=False,  # type: bool
                          debug=False  # type: bool
                          ):
        # type: (...) -> Optional[WireArray]
        """Connect all given WireArrays to the given track(s).

        All given wires should be on adjcent layer of the track.

        Parameters
        ----------
        wire_arr_list : Union[WireArray, List[WireArray]]
            list of WireArrays to connect to track.
        track_id : TrackID
            TrackID that specifies the track(s) to connect the given wires to.
        wire_lower : Optional[Union[float, int]]
            if given, extend wire(s) to this lower coordinate.
        wire_upper : Optional[Union[float, int]]
            if given, extend wire(s) to this upper coordinate.
        track_lower : Optional[Union[float, int]]
            if given, extend track(s) to this lower coordinate.
        track_upper : Optional[Union[float, int]]
            if given, extend track(s) to this upper coordinate.
        fill_margin : Union[int, float]
            minimum margin between wires and fill.
        fill_type : str
            fill connection type.  Either 'VDD' or 'VSS'.  Defaults to 'VSS'.
        unit_mode: bool
            True if track_lower/track_upper/fill_margin is given in resolution units.
        debug : bool
            True to print debug messages.

        Returns
        -------
        wire_arr : Optional[WireArray]
            WireArray representing the tracks created.  None if nothing to do.
        """
        if isinstance(wire_arr_list, WireArray):
            # convert to list.
            wire_arr_list = [wire_arr_list]
        else:
            pass

        if not wire_arr_list:
            # do nothing
            return None

        grid = self.grid
        res = grid.resolution

        if not unit_mode:
            fill_margin = int(round(fill_margin / res))
            if track_upper is not None:
                track_upper = int(round(track_upper / res))
            if track_lower is not None:
                track_lower = int(round(track_lower / res))

        # find min/max track Y coordinates
        tr_layer_id = track_id.layer_id
        wl, wu = track_id.get_bounds(grid, unit_mode=True)
        if wire_lower is not None:
            if not unit_mode:
                wire_lower = int(round(wire_lower / res))
            wl = min(wire_lower, wl)

        if wire_upper is not None:
            if not unit_mode:
                wire_upper = int(round(wire_upper / res))
            wu = max(wire_upper, wu)

        # get top wire and bottom wire list
        top_list = []
        bot_list = []
        for wire_arr in wire_arr_list:
            cur_layer_id = wire_arr.layer_id
            if cur_layer_id == tr_layer_id + 1:
                top_list.append(wire_arr)
            elif cur_layer_id == tr_layer_id - 1:
                bot_list.append(wire_arr)
            else:
                raise ValueError('WireArray layer %d cannot connect to layer %d' % (cur_layer_id, tr_layer_id))

        # connect wires together
        top_wire_list = self.connect_wires(top_list, lower=wl, upper=wu, fill_margin=fill_margin,
                                           fill_type=fill_type, unit_mode=True, debug=debug)
        bot_wire_list = self.connect_wires(bot_list, lower=wl, upper=wu, fill_margin=fill_margin,
                                           fill_type=fill_type, unit_mode=True, debug=debug)

        # draw vias
        for w_layer_id, wire_list in ((tr_layer_id + 1, top_wire_list), (tr_layer_id - 1, bot_wire_list)):
            for wire_arr in wire_list:
                for wlayer, box_arr in wire_arr.wire_arr_iter(grid):
                    track_lower, track_upper = self._draw_via_on_track(wlayer, box_arr, track_id,
                                                                       tl_unit=track_lower, tu_unit=track_upper)

        # draw tracks
        result = WireArray(track_id, track_lower * res, track_upper * res)
        for layer_name, bbox_arr in result.wire_arr_iter(self.grid):
            self.add_rect(layer_name, bbox_arr)

        self._used_tracks.add_wire_arrays(grid, result, fill_margin=fill_margin, fill_type=fill_type, unit_mode=True)
        return result

    def connect_differential_tracks(self, pwarr_list,  # type: Union[WireArray, List[WireArray]]
                                    nwarr_list,  # type: Union[WireArray, List[WireArray]]
                                    tr_layer_id,  # type: int
                                    ptr_idx,  # type: Union[int, float]
                                    ntr_idx,  # type: Union[int, float]
                                    width=1,  # type: int
                                    track_lower=None,  # type: Optional[Union[float, int]]
                                    track_upper=None,  # type: Optional[Union[float, int]]
                                    fill_margin=0,  # type: Union[int, float]
                                    fill_type='VSS',  # type: str
                                    unit_mode=False,  # type: bool
                                    debug=False  # type: bool
                                    ):
        # type: (...) -> Tuple[Optional[WireArray], Optional[WireArray]]
        """Connect the given differential wires to two tracks symmetrically.

        This method makes sure the connections are symmetric and have identical parasitics.
        This method only works if all given wires are on the same layer and have the same width.

        Parameters
        ----------
        pwarr_list : Union[WireArray, List[WireArray]]
            positive signal wires to connect.
        nwarr_list : Union[WireArray, List[WireArray]]
            negative signal wires to connect.
        tr_layer_id : int
            track layer ID.
        ptr_idx : Union[int, float]
            positive track index.
        ntr_idx : Union[int, float]
            negative track index.
        width : int
            track width in number of tracks.
        track_lower : Optional[Union[float, int]]
            if given, extend track(s) to this lower coordinate.
        track_upper : Optional[Union[float, int]]
            if given, extend track(s) to this upper coordinate.
        fill_margin : Union[int, float]
            minimum margin between wires and fill.
        fill_type : str
            fill connection type.  Either 'VDD' or 'VSS'.  Defaults to 'VSS'.
        unit_mode: bool
            True if track_lower/track_upper/fill_margin is given in resolution units.
        debug : bool
            True to print debug messages.

        Returns
        -------
        p_track : Optional[WireArray]
            the positive track.
        n_track : Optional[WireArray]
            the negative track.
        """
        if isinstance(pwarr_list, WireArray):
            pwarr_list = [pwarr_list]
        else:
            pass
        if isinstance(nwarr_list, WireArray):
            nwarr_list = [nwarr_list]
        else:
            pass

        if not pwarr_list:
            return None, None

        grid = self.grid
        res = grid.resolution

        if not unit_mode:
            fill_margin = int(round(fill_margin / res))
            if track_lower is not None:
                track_lower = int(round(track_lower / res))
            if track_upper is not None:
                track_upper = int(round(track_upper / res))

        # error checking
        w_lay_id = pwarr_list[0].layer_id
        w_width = pwarr_list[0].width
        if w_lay_id != tr_layer_id + 1 and w_lay_id != tr_layer_id - 1:
            raise ValueError('Cannot connect wire on layer %d to track on layer %d' % (w_lay_id, tr_layer_id))
        # error checking + get track lower and upper coordinates.
        tr_lower, tr_upper = None, None
        for warr in chain(pwarr_list, nwarr_list):
            warr_tid = warr.track_id  # type: TrackID
            if warr_tid.layer_id != w_lay_id:
                raise ValueError('WireArray layer = %d != %d' % (warr.layer_id, w_lay_id))
            if warr_tid.width != w_width:
                raise ValueError('WireArray width = %d != %d' % (warr.width, w_width))
            warr_bounds = warr_tid.get_bounds(grid, unit_mode=True)
            if tr_lower is None:
                tr_lower = warr_bounds[0]
            else:
                tr_lower = min(tr_lower, warr_bounds[0])
            if tr_upper is None:
                tr_upper = warr_bounds[1]
            else:
                tr_upper = max(tr_upper, warr_bounds[1])

        pos_tid = TrackID(tr_layer_id, ptr_idx, width)
        neg_tid = TrackID(tr_layer_id, ntr_idx, width)
        pos_lower, pos_upper = pos_tid.get_bounds(grid, unit_mode=True)
        neg_lower, neg_upper = neg_tid.get_bounds(grid, unit_mode=True)
        w_lower = min(pos_lower, neg_lower)
        w_upper = max(pos_upper, neg_upper)
        tr_width = pos_upper - pos_lower
        tr_dir = grid.get_direction(tr_layer_id)

        # make test via to get extensions
        w0, w1 = grid.get_wire_bounds(w_lay_id, 0, width=w_width, unit_mode=True)
        w_width = w1 - w0
        if tr_dir == 'x':
            via_box = BBox(0, 0, w_width, tr_width, res, unit_mode=True)
        else:
            via_box = BBox(0, 0, tr_width, w_width, res, unit_mode=True)
        if tr_layer_id > w_lay_id:
            vtop_layer = grid.get_layer_name(tr_layer_id, 0)
            vbot_layer = grid.get_layer_name(w_lay_id, 0)
            bot_dir = grid.get_direction(w_lay_id)
            via_test = self.add_via(via_box, vbot_layer, vtop_layer, bot_dir)
            vw_box = via_test.bottom_box
            vt_box = via_test.top_box
        else:
            vbot_layer = grid.get_layer_name(tr_layer_id, 0)
            vtop_layer = grid.get_layer_name(w_lay_id, 0)
            bot_dir = grid.get_direction(tr_layer_id)
            via_test = self.add_via(via_box, vbot_layer, vtop_layer, bot_dir)
            vw_box = via_test.top_box
            vt_box = via_test.bottom_box
        via_test.destroy()

        # calculate extension
        if tr_dir == 'x':
            t_ext = (vt_box.width_unit - w_width) // 2
            w_ext = (vw_box.height_unit - tr_width) // 2
        else:
            t_ext = (vt_box.height_unit - w_width) // 2
            w_ext = (vw_box.width_unit - tr_width) // 2
        w_lower -= w_ext
        w_upper += w_ext
        tr_lower -= t_ext
        tr_upper += t_ext

        if track_lower is not None:
            tr_lower = min(tr_lower, track_lower)
        if track_upper is not None:
            tr_upper = max(tr_upper, track_upper)

        # draw differential tracks
        pans = self.connect_to_tracks(pwarr_list, pos_tid, wire_lower=w_lower, wire_upper=w_upper,
                                      track_lower=tr_lower, track_upper=tr_upper, fill_margin=fill_margin,
                                      fill_type=fill_type, unit_mode=True, debug=debug)
        nans = self.connect_to_tracks(nwarr_list, neg_tid, wire_lower=w_lower, wire_upper=w_upper,
                                      track_lower=tr_lower, track_upper=tr_upper, fill_margin=fill_margin,
                                      fill_type=fill_type, unit_mode=True, debug=debug)
        return pans, nans


# noinspection PyAbstractClass
class MicroTemplate(with_metaclass(abc.ABCMeta, TemplateBase)):
    """The base class of all micro templates.

    Parameters
    ----------
    temp_db : TemplateDB
            the template database.
    lib_name : str
        the layout library name.
    params : Dict[str, Any]
        the parameter values.
    used_names : Set[str]
        a set of already used cell names.
    **kwargs
        dictionary of optional parameters.  See documentation of
        :class:`bag.layout.template.TemplateBase` for details.
    """

    def __init__(self, temp_db, lib_name, params, used_names, **kwargs):
        # type: (TemplateDB, str, Dict[str, Any], Set[str], **Any) -> None
        super(MicroTemplate, self).__init__(temp_db, lib_name, params, used_names, **kwargs)

    @classmethod
    def is_micro(cls):
        # type: () -> bool
        """Returns True if this template is a micro template.

        Override this method to return True if this is a micro template.

        Returns
        -------
        is_primitive : bool
            True if this template is a primitive template.
        """
        return True
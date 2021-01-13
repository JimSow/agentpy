"""
Agentpy Output Module
Content: DataDict class for output data
"""

import pandas as pd
import os
from os import listdir, makedirs
from os.path import getmtime, join

from .tools import AttrDict, make_list, AgentpyError
import json
import numpy as np


class NpEncoder(json.JSONEncoder):
    """ Adds support for numpy number formats to json. """
    # By Jie Yang https://stackoverflow.com/a/57915246
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        else:
            return super(NpEncoder, self).default(obj)


def _last_exp_id(name, path):
    """ Identifies existing experiment data and return highest id. """

    exp_id = 0
    output_dirs = listdir(path)
    exp_dirs = [s for s in output_dirs if name in s]
    if exp_dirs:
        ids = [int(s.split('_')[-1]) for s in exp_dirs]
        exp_id = max(ids)
    return exp_id


class DataDict(AttrDict):
    """ Dictionary for recorded simulation data.

    Generated by :class:`Model`, :class:`Experiment`, or :func:`load`.
    Dictionary items can be defined and accessed like attributes.
    Attributes can differ from the standard ones listed below.

    Attributes:
        log (dict): Meta-data of the simulation
            (e.g. name, time-stamps, settings, etc.).
        parameters (dict, pandas.DataFrame, or DataDict):
            Parameters that have been used for the simulation.
        variables (pandas.DataFrame or DataDict)):
            Dynamic variables, seperated per object type,
            which can be recorded once per time-step with :func:`record`.
        measures (pandas.DataFrame): Evaluation measures,
            which can be recorded once per run with :func:`measure`.
    """

    def __repr__(self, indent=False):
        rep = ""
        if not indent:
            rep += "DataDict {"
        i = '    ' if indent else ''
        for k, v in self.items():
            rep += f"\n{i}'{k}': "
            if isinstance(v, (int, float, np.integer, np.floating)):
                rep += f"{v} {type(v)}"
            elif isinstance(v, str):
                x0 = f"(length {len(v)})"
                x = f"...' {x0}" if len(v) > 20 else "'"
                rep += f"'{v[:30]}{x} {type(v)}"
            elif isinstance(v, pd.DataFrame):
                lv = len(list(v.columns))
                rv = len(list(v.index))
                rep += f"DataFrame with {lv} " \
                       f"variable{'s' if lv != 1 else ''} " \
                       f"and {rv} row{'s' if rv != 1 else ''}"
            elif isinstance(v, DataDict):
                rep += f"{v.__repr__(indent=True)}"
            elif isinstance(v, dict):
                lv = len(list(v.keys()))
                rep += f"Dictionary with {lv} key{'s' if lv != 1 else ''}"
            elif isinstance(v, list):
                lv = len(v)
                rep += f"List with {lv} entr{'ies' if lv != 1 else 'y'}"
            else:
                rep += f"Object of type {type(v)}"
        if not indent:
            rep += "\n}"
        return rep

    def _short_repr(self):
        len_ = len(self.keys())
        return f"DataDict {{{len_} entr{'y' if len_ == 1 else 'ies'}}}"

    def __eq__(self, other):
        """ Check equivalence of two DataDicts."""
        if not isinstance(other, DataDict):
            return False
        for key, item in self.items():
            if key not in other:
                return False
            if isinstance(item, pd.DataFrame):
                if not self[key].equals(other[key]):
                    return False
            elif not self[key] == other[key]:
                return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def _combine_vars(self, obj_types='all', var_keys='all'):
        """ Returns pandas dataframe with combined variables """

        # Retrieve variables
        if 'variables' in self:
            vs = self['variables']
        else:
            return None
        if isinstance(vs, pd.DataFrame):
            return vs  # Return df if vs is already a df
        elif isinstance(vs, DataDict) and len(vs.keys()) == 1:
            return list(vs.values())[0]  # Return df if vs has only one entry
        elif isinstance(vs, (dict,DataDict)):
            df_dict = dict(vs)  # Convert to dict if vs is DataDict
        else:
            raise TypeError("DataDict.variables must be of type dict,"
                             "agentpy.DataDict, or pandas.DataFrame.")

        # Remove dataframes that don't include any of the selected var_keys
        if var_keys != 'all':
            df_dict = {k: v for k, v in df_dict.items()
                       if any(x in v.columns for x in make_list(var_keys))}

        # Select object types
        if obj_types != 'all':
            df_dict = {k: v for k, v in df_dict.items()
                       if k in make_list(obj_types)}

        # Add 'obj_id' before 't' for model df
        model_type = self.log['model_type']
        if model_type in list(df_dict.keys()):
            df = df_dict[model_type]
            df['obj_id'] = 0
            indexes = list(df.index.names)
            indexes.insert(-1, 'obj_id')
            df = df.reset_index()
            df = df.set_index(indexes)
            df_dict[model_type] = df

        # Return none if empty
        if df_dict == {}:
            return None

        # Create dataframe
        df = pd.concat(df_dict)  # Dict keys (obj_type) will be added to index
        df.index = df.index.set_names('obj_type', level=0)  # Rename new index

        # Select var_keys
        if var_keys != 'all':
            # make_list prevents conversion to pd.Series for single value
            df = df[make_list(var_keys)]

        return df

    def _combine_pars(self, varied=True, fixed=True):
        """ Returns pandas dataframe with parameters and run_id """
        # Case 0: Cancel if there are no parameters
        if 'parameters' not in self:
            return None
        # Case 1: There is a subdict with fixed & combined
        dfp = None
        if isinstance(self.parameters, DataDict):
            dfp = pd.DataFrame()
            if varied:
                dfp = self.parameters.varied.copy()
            if fixed:
                for k, v in self.parameters.fixed.items():
                    dfp[k] = v
        # Case 2: There is a dict with fixed parameters
        elif isinstance(self.parameters, dict):
            if fixed:
                dfp = pd.DataFrame({k: [v] for k, v in self.parameters.items()})
        # Case 3: There is a dataframe with varied parameters
        elif isinstance(self.parameters, pd.DataFrame):
            if varied:
                dfp = self.parameters.copy()
        # Case 4: No parameters have been selected
        else:
            raise TypeError("DataDict.parameters must be of type"
                            "DataDict, dict, or pandas.DataFrame.")
        # Case 5: Cancel if no parameters have been selected
        if dfp is None or dfp.shape == (0, 0):
            return None
        # Case 1-3: Multiply for iterations, set new index, and return
        if 'iterations' in self.log and self.log['iterations'] > 1:
            dfp = pd.concat([dfp] * self.log['iterations'])
        dfp = dfp.reset_index(drop=True)
        dfp.index.name = 'run_id'
        return dfp

    def arrange_measures(self, variables=None, measures='all',
                         parameters='varied', obj_types='all',
                         scenarios='all', index=False):
        """ Returns a dataframe with measures and varied parameters.
        See :func:`DataDict.arrange` for further information."""
        return self.arrange(variables=variables, measures=measures,
                            parameters=parameters, obj_types=obj_types,
                            scenarios=scenarios, index=index)

    def arrange_variables(self, variables='all', measures=None,
                          parameters='varied', obj_types='all',
                          scenarios='all', index=False):
        """ Returns a dataframe with variables and varied parameters.
        See :func:`DataDict.arrange` for further information."""
        return self.arrange(variables=variables, measures=measures,
                            parameters=parameters, obj_types=obj_types,
                            scenarios=scenarios, index=index)

    def arrange(self, variables=None, measures=None, parameters=None,
                obj_types='all', scenarios='all', index=False):
        """ Combines and/or filters data based on passed arguments.

        Arguments:
            variables (str or list of str, optional):
                Variables to include in the new dataframe (default None).
                If 'all', all are selected.
            measures (str or list of str, optional):
                Measures to include in the new dataframe (default None).
                If 'all', all are selected.
            parameters (str or list of str, optional):
                Parameters to include in the new dataframe (default None).
                If 'fixed', all fixed parameters are selected.
                If 'varied', all varied parameters are selected.
                If 'all', all are selected.
            obj_types (str or list of str, optional):
                Agent and/or environment types to include in the new dataframe.
                Note that the selected object types will only be included
                if at least one of their variables is declared in 'variables'.
                If 'all', all are selected (default).
            scenarios (str or list of str, optional):
                Scenarios to include in the new dataframe.
                If 'all', all are selected (default).
            index (bool, optional):
                Whether to keep original multi-index structure (default False).

        Returns:
            pandas.DataFrame: The arranged dataframe
        """

        dfv = dfm = dfp = df = None

        # Step 1: Variables
        if variables is not None:
            dfv = self._combine_vars(obj_types, variables)

        # Step 2: Measures
        if measures is not None:
            dfm = self.measures
            if measures is not 'all':  # Select measure keys
                # make_list prevents conversion to pd.Series for single value
                dfm = dfm[make_list(measures)]

        # Step 3: Parameters
        if parameters is not None:
            varied = False if parameters == 'static' else True
            static = False if parameters == 'varied' else True
            dfp = self._combine_pars(varied, static)
            if parameters not in ['all', 'varied', 'static']:
                # Select parameter keys
                # make_list prevents conversion to pd.Series for single value
                dfp = dfp[make_list(parameters)]

        # Step 4: Combine dataframes
        if dfv is not None and dfm is not None:
            # Combine variables & measures
            index_keys = dfv.index.names
            dfm = dfm.reset_index()
            dfv = dfv.reset_index()
            df = pd.concat([dfm, dfv])
            df = df.set_index(index_keys)
        elif dfv is not None:
            df = dfv
        elif dfm is not None:
            df = dfm
        if dfp is not None:
            if df is None:
                df = dfp
            else:  # Combine df with parameters
                if len(dfp) > 1:  # If multi run, add parameters by run_id
                    if df is not None and isinstance(df.index, pd.MultiIndex):
                        dfp = dfp.reindex(df.index, level='run_id')
                    df = pd.concat([df, dfp], axis=1)
                else:  # Elif single run, add parameters as columns
                    for k, v in dfp.items():
                        # dfp is a dataframe, items returns columns, Series
                        df[k] = v[0]
        if df is None:
            return None

        # Step 5: Select scenarios
        if scenarios != 'all' and 'scenario' in df.index.names:
            scenarios = make_list(scenarios)  # noqa
            df = df.query("scenario in @scenarios")

        # Step 6: Reset index
        if not index:
            df = df.reset_index()

        return df

    def save(self, exp_name=None, exp_id=None, path='ap_output', display=True):

        """ Writes data to directory `{path}/{exp_name}_{exp_id}/`.
        Works only for entries that are of type :class:`DataDict`,
        :class:`pandas.DataFrame`, or serializable with JSON
        (int, float, str, dict, list). Numpy objects will be converted
        to standard objects, if possible.

        Arguments:
            exp_name (str, optional): Name of the experiment to be saved.
                If none is passed, `self.log['name']` is used.
            exp_id (int, optional): Number of the experiment.
                If none is passed, a new id is generated.
            path (str, optional): Target directory (default 'ap_output').
            display (bool, optional): Display saving progress (default True).

        """

        # Create output directory if it doesn't exist
        if path not in listdir():
            makedirs(path)

        # Set exp_name
        if exp_name is None:
            if 'log' in self and 'name' in self.log:
                exp_name = self.log['name']
            else:
                exp_name = 'Unnamed'

        exp_name = exp_name.replace(" ", "_")

        # Set exp_id
        if exp_id is None:
            exp_id = _last_exp_id(exp_name, path) + 1

        # Create new directory for output
        path = f'{path}/{exp_name}_{exp_id}'
        makedirs(path)

        # Save experiment data
        for key, output in self.items():

            if isinstance(output, pd.DataFrame):
                output.to_csv(f'{path}/{key}.csv')

            if isinstance(output, DataDict):
                for k, o in output.items():

                    if isinstance(o, pd.DataFrame):
                        o.to_csv(f'{path}/{key}_{k}.csv')
                    elif isinstance(o, dict):
                        with open(f'{path}/{key}_{k}.json', 'w') as fp:
                            json.dump(o, fp, cls=NpEncoder)

            else:  # Use JSON for other object types
                try:
                    with open(f'{path}/{key}.json', 'w') as fp:
                        json.dump(output, fp, cls=NpEncoder)
                except TypeError as e:
                    print(f"Warning: Object '{key}' could not be saved. "
                          f"(Reason: {e})")
                    os.remove(f'{path}/{key}.json')

            # TODO Support grids & graphs
            # elif t == nx.Graph:
            #    nx.write_graphml(output, f'{path}/{key}.graphml')

        if display:
            print(f"Data saved to {path}")

    def _load(self, exp_name=None, exp_id=None,
              path='ap_output', display=True):

        def load_file(path, file, display):
            if display:
                print(f'Loading {file} - ', end='')
            i_cols = ['sample_id', 'run_id', 'scenario',
                      'env_key', 'agent_id', 'obj_id', 't']
            ext = file.split(".")[-1]
            path = path + file
            try:
                if ext == 'csv':
                    obj = pd.read_csv(path) # Convert .csv into DataFrane
                    index = [i for i in i_cols if i in obj.columns]
                    if index:  # Set potential index columns
                        obj = obj.set_index(index)
                elif ext == 'json':
                    # Convert .json with json decoder
                    with open(path, 'r') as fp:
                        obj = json.load(fp)
                    # Convert dict to AttrDict
                    if isinstance(obj, dict):
                        obj = AttrDict(obj)
                # TODO Support grids & graphs
                # elif ext == 'graphml':
                #    self[key] = nx.read_graphml(path)
                else:
                    raise ValueError(f"File type '{ext}' not supported")
                if display:
                    print('Successful')
                return obj
            except Exception as e:
                print(f'Error: {e}')

        # Prepare for loading
        if exp_name is None:
            # Choose latest modified experiment
            exp_names = listdir(path)
            paths = [join(path, d) for d in exp_names]
            latest_exp = exp_names[paths.index(max(paths, key=getmtime))]
            exp_name = latest_exp.rsplit('_', 1)[0]

        exp_name = exp_name.replace(" ", "_")
        if not exp_id:
            exp_id = _last_exp_id(exp_name, path)
            if exp_id == 0:
                raise FileNotFoundError(f"No experiment found with "
                                        f"name '{exp_name}' in path '{path}'")
        path = f'{path}/{exp_name}_{exp_id}/'
        if display:
            print(f'Loading from directory {path}')

        # Loading data
        for file in listdir(path):
            if 'variables_' in file:
                if 'variables' not in self:
                    self['variables'] = DataDict()
                ext = file.split(".")[-1]
                key = file[:-(len(ext) + 1)].replace('variables_', '')
                self['variables'][key] = load_file(path, file, display)
            elif 'parameters_' in file:
                ext = file.split(".")[-1]
                key = file[:-(len(ext) + 1)].replace('parameters_', '')
                if 'parameters' not in self:
                    self['parameters'] = DataDict()
                self['parameters'][key] = load_file(path, file, display)
            else:
                ext = file.split(".")[-1]
                key = file[:-(len(ext) + 1)]
                self[key] = load_file(path, file, display)
        return self


def load(exp_name=None, exp_id=None, path='ap_output', display=True):
    """ Reads output data from directory `{path}/{exp_name}_{exp_id}/`.

        Arguments:
            exp_name (str, optional): Experiment name.
                If none is passed, the most recent experiment is chosen.
            exp_id (int, optional): Id number of the experiment.
                If none is passed, the highest available id used.
            path (str, optional): Target directory (default 'ap_output').
            display (bool, optional): Display loading progress (default True).

        Returns:
            DataDict: The loaded data from the chosen experiment.
    """
    return DataDict()._load(exp_name, exp_id, path, display)

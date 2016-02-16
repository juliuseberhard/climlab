import numpy as np
import copy
from climlab import constants as const
from climlab.process.process import Process
from climlab.utils.walk import walk_processes


class TimeDependentProcess(Process):
    """A generic parent class for all time-dependent processes.
    
    ``TimeDependentProcess`` is a child of the 
    :class:`~climlab.process.process.Process` class and therefore inherits
    all those attributes.
    
    **Initialization parameters** \n
    
    An instance of ``TimeDependentProcess`` is initialized with the following 
    arguments *(for detailed information see Object attributes below)*:
    
    :param float timestep:  specifies the timestep of the object
    :param str time_type:   how time-dependent-process should be computed. 
                            Set to ``'explicit'`` by default.            
    :param bool topdown:    whether geneterate *process_types* in regular or 
                            in reverse order.
                            Set to ``True`` by default.  
    
    **Object attributes** \n
    
    Additional to the parent class :class:`~climlab.process.process.Process`
    following object attributes are generated during initialization:
    
    :ivar bool has_process_type_list:    
                            information whether attribute *process_types* 
                            (which is needed for :func:`compute` and build in
                            :func:`_build_process_type_list`)
                            exists or not. Attribute is set to ``'False'`` 
                            during initialization.   
    :ivar bool topdown:     information whether the list *process_types* (which 
                            contains all processes and sub-processes) should be 
                            generated in regular or in reverse order.
                            See :func:`_build_process_type_list`. 
    :ivar dict timeave:     a time averaged collection of all states and diagnostic 
                            processes over the timeperiod that 
                            :func:`integrate_years` has been called for last.          
    :ivar dict tendencies:  computed difference in a timestep for each state. 
                            See :func:`compute` for details.
    :ivar str time_type:    how time-dependent-process should be computed. 
                            Possible values are: ``'explicit'``, ``'implicit'``,
                            ``'diagnostic'``, ``'adjustment'``.
    :ivar dict time:        a collection of all time-related attributes of the process. 
                            The dictionary contains following items:
                            
        * ``'timestep'``: see initialization parameter
        * ``'num_steps_per_year'``: see :func:`set_timestep` and :func:`timestep` for details
        * ``'day_of_year_index'``: counter how many steps have been integrated in current year
        * ``'steps'``: counter how many steps have been integrated in total,
        * ``'days_elapsed'``: time counter for days,
        * ``'years_elapsed'``: time counter for years,
        * ``'days_of_year'``: array which holds the number of numerical steps per year, expressed in days

    """
    def __init__(self, time_type='explicit', timestep=None, topdown=True, **kwargs):
        # Create the state dataset
        super(TimeDependentProcess, self).__init__(**kwargs)
        self.tendencies = {}
        for name, var in self.state.iteritems():
            self.tendencies[name] = var * 0.
        self.timeave = {}
        if timestep is None:
            self.set_timestep()
        else:
            self.set_timestep(timestep=timestep)
        self.time_type = time_type
        self.topdown = topdown
        self.has_process_type_list = False

    @property
    def timestep(self):
        """The amount of time over which :func:`step_forward` is integrating in unit seconds.

        :getter: Returns the object timestep which is stored in ``self.param['timestep']``.
        :setter: Sets the timestep to the given input. See also :func:`set_timestep`.
        :type: float
        
        """
        return self.param['timestep']       
    @timestep.setter
    def timestep(self, value):
        num_steps_per_year = const.seconds_per_year / value
        timestep_days = value / const.seconds_per_day
        days_of_year = np.arange(0., const.days_per_year, timestep_days)
        self.time = {'timestep': value,
                     'num_steps_per_year': num_steps_per_year,
                     'day_of_year_index': 0,
                     'steps': 0,
                     'days_elapsed': 0,
                     'years_elapsed': 0,
                     'days_of_year': days_of_year}
        self.param['timestep'] = value

    def set_timestep(self, timestep=const.seconds_per_day, num_steps_per_year=None):
        """Calculates the timestep in unit seconds
        and calls the setter function of :func:`timestep`
        
        :param timestep:            the amount of time over which 
                                    :func:`step_forward` is integrating 
                                    in unit seconds
        :type timestep:             float
        :param num_steps_per_year:  a number of steps per calendar year
        :type num_steps_per_year:   float
        
        If the parameter *num_steps_per_year* is specified and not ``None``, 
        the timestep is calculated accordingly and therefore the given input
        parameter *timestep* is ignored.
        
        """
        if num_steps_per_year is not None:
            timestep = const.seconds_per_year / num_steps_per_year
        # Need a more sensible approach for annual cycle stuff
        self.timestep = timestep

    def compute(self):
        """Computes the tendencies for all state variables given current state 
        and specified input.
        
        The function first computes all diagnostic processes as they may effect
        all the other processes (such as change in solar distribution).
        After all they don't produce any tendencies directly. Subsequently
        all tendencies and diagnostics for all explicit processes are computed.
        
        Tendencies due to implicit and adjustment processes need to be
        calculated from a state that is already adjusted after explicit 
        alteration. So the explicit tendencies are applied to the states 
        temporarily. Now all tendencies from implicit processes are calculated 
        through matrix inversions and same like the explicit tendencies applied
        to the states temporarily. Subsequently all instantaneous adjustments 
        are computed.
        
        Then the changes made to the states from explicit and implicit 
        processes are removed again as this :func:`compute` function is
        supposed to calculate only tendencies and not applying them to the states.
        
        Finally all calculated tendencies from all processes are collected 
        for each state, summed up and stored in the dictionary 
        ``self.tendencies``, which is an attribute of the time-dependent-process 
        object for which the :func:`func` method has been called.
        """
        if not self.has_process_type_list:
            self._build_process_type_list()
        # First compute all strictly diagnostic processes
        ignored = self._compute_type('diagnostic')
        # Compute tendencies and diagnostics for all explicit processes
        tendencies_explicit = self._compute_type('explicit')
        #  Tendencies due to implicit and adjustment processes need to be
        #  calculated from a state that is already adjusted after explicit stuff
        #  So apply the tendencies temporarily and then remove them again
        for name, var in self.state.iteritems():
            var += tendencies_explicit[name] * self.timestep
        # Now compute all implicit processes -- matrix inversions
        tendencies_implicit = self._compute_type('implicit')
        for name, var in self.state.iteritems():
            var += tendencies_implicit[name] * self.timestep
        # Finally compute all instantaneous adjustments
        #  and express in terms of discrete timestep
        adjustments = self._compute_type('adjustment')
        tendencies_adjustment = {}
        for name in adjustments:
            tendencies_adjustment[name] = adjustments[name] / self.timestep
        #  Now remove the changes from the model state
        for name, var in self.state.iteritems():
            var -= ( (tendencies_implicit[name] + tendencies_explicit[name]) *
                    self.timestep)
        # Finally sum up all the tendencies from all processes
        self.tendencies = {}
        for varname in self.state:
            self.tendencies[varname] = 0. * self.state[varname]
        for tend_dict in [tendencies_explicit,
                          tendencies_implicit,
                          tendencies_adjustment]:
            for name in tend_dict:
                self.tendencies[name] += tend_dict[name]
        #
        #
        # if (self.topdown and self.time_type is 'explicit'):
        # 	 #  tendencies is dictionary with same names as state variables
        #     tendencies = self._compute()
        #     for name, proc in self.subprocess.iteritems():
        #         proc.compute()
        #         for varname, tend in proc.tendencies.iteritems():
        #             tendencies[varname] += tend
        # else:
        # #  make a new dictionary to hold tendencies on state variables
        #     tendencies = {}
        #     for varname in self.state:
        #         tendencies[varname] = 0. * self.state[varname]
        #     for name, proc in self.subprocess.iteritems():
        #         proc.compute()
        #         for varname, tend in proc.tendencies.iteritems():
        #             tendencies[varname] += tend
		# 		#diagnostics.merge(diag_sub)
        #     parent_tendencies = self._compute()
        #     for varname, tend in parent_tendencies.iteritems():
        #         tendencies[varname] += tend
        # self.tendencies = tendencies
        # #  pass diagnostics up the process tree
        # #  actually don't. This should be done explicitly by parent processes
        # #   but only where it is meaningful.
        # #for name, proc in self.subprocess.iteritems():
        # #    self.diagnostics.update(proc.diagnostics)

    def _compute_type(self, proctype):
        """Computes tendencies due to all subprocesses of given type ``'proctype'``.
        
        """
        tendencies = {}
        for varname in self.state:
            tendencies[varname] = 0. * self.state[varname]
        for proc in self.process_types[proctype]:
            proctend = proc._compute()
            for varname, tend in proctend.iteritems():
                tendencies[varname] += tend
        return tendencies

    def _compute(self):
        # where the tendencies are actually computed...
        #  needs to be implemented for each daughter class
        #  needs to return a dictionary with same keys as self.state
        tendencies = {}
        for name, value in self.state.iteritems():
            tendencies[name] = value * 0.
        return tendencies

    def _build_process_type_list(self):
        """Generates lists of processes organized by process type.
        
        Following object attributes are generated or updated:
    
        :ivar dict process_types:   a dictionary with entries:  
                                    ``'diagnostic'``, ``'explicit'``,
                                    ``'implicit'`` and ``'adjustment'`` which
                                    point to a list of processes according to
                                    the process types.
        
        The ``process_types`` dictionary is created while walking 
        through the processes with :func:`~climlab.utils.walk.walk_processes`
        
        """
        self.process_types = {'diagnostic': [], 'explicit': [], 'implicit': [], 'adjustment': []}
        for name, proc, level in walk_processes(self, topdown=self.topdown):
            self.process_types[proc.time_type].append(proc)
        self.has_process_type_list = True

    def step_forward(self):
        """Updates state variables with computed tendencies.
        
        Calls the :func:`compute` method to get current tendencies for all
        process states. Multiplied with the timestep and added up to the state
        variables is updating all model states.
        
        """
        self.compute()
        #  Total tendency is applied as an explicit forward timestep
        # (already accounting properly for order of operations in compute() )
        for name, var in self.state.iteritems():
            var += self.tendencies[name] * self.param['timestep']

        # Update all time counters for this and all subprocesses in the tree
        for name, proc, level in walk_processes(self):
            proc._update_time()

        # if not self.has_process_type_list:
        #     self._build_process_type_list()
        # # First compute all strictly diagnostic processes
        # for proc in self.process_types['diagnostic']:
        #     proc.compute()
        # # Compute tendencies and diagnostics for all explicit processes
        # for proc in self.process_types['explicit']:
        #     proc.compute()
        # # Update state variables using all explicit tendencies
        # #  Tendencies are d/dt(state) -- so just multiply by timestep for forward time
        # for proc in self.process_types['explicit']:
        #     for varname in proc.state.keys():
        #         try: proc.state[varname] += (proc.tendencies[varname] *
        #                                      self.param['timestep'])
        #         except: pass
        # # Now compute all implicit processes -- matrix inversions
        # for proc in self.process_types['implicit']:
        #     proc.compute()
        #     for varname in proc.state.keys():
        #         try: proc.state[varname] += proc.adjustment[varname]
        #         except: pass
        # # Adjustment processes change the state instantaneously
        # for proc in self.process_types['adjustment']:
        #     proc.compute()
        #     for varname, value in proc.state.iteritems():
        #         #proc.set_state(varname, proc.adjusted_state[varname])
        #         try: proc.state[varname] += proc.adjustment[varname]
        #         except: pass
        # # Gather all diagnostics
        # for name, proc, level in walk_processes(self):
        #     self.diagnostics.update(proc.diagnostics)
        #     proc._update_time()

    def compute_diagnostics(self, num_iter=3):
        """Compute all tendencies and diagnostics, but don't update model state.
        By default it will call compute() 3 times to make sure all
        subprocess coupling is accounted for. The number of iterations can
        be changed with the input argument.
        
        """
        for n in range(num_iter):
            self.compute()

    def _update_time(self):
        """Increments the timestep counter by one.
        
        Furthermore ``self.time['days_elapsed']`` and
        ``self.time['num_steps_per_year']`` are updated.
        
        The function is called by the timestepping routines.
        
        """
        self.time['steps'] += 1
        # time in days since beginning
        self.time['days_elapsed'] += self.time['timestep'] / const.seconds_per_day
        if self.time['day_of_year_index'] >= self.time['num_steps_per_year']-1:
            self._do_new_calendar_year()
        else:
            self.time['day_of_year_index'] += 1

    def _do_new_calendar_year(self):
        """This function is called once at the end of every calendar year.
        
        It updates ``self.time['years_elapsed']`` and 
        ``self.time['day_of_year_index']``
        """
        self.time['day_of_year_index'] = 0  # back to Jan. 1
        self.time['years_elapsed'] += 1

    def integrate_years(self, years=1.0, verbose=True):
        """Integrates the model by a given number of years.
        
        :param years: integration time for the model in years
        :type years: float
        
        :param verbose: information whether model time details should be 
                        printed.
        :type verbose: boolean
        
        It calls :func:`step_forward` repetitively and calculates a time 
        averaged value over the integrated period for every model state and all
        diagnostics processes.
        """
        days = years * const.days_per_year
        numsteps = int(self.time['num_steps_per_year'] * years)
        if verbose:
            print("Integrating for " + str(numsteps) + " steps, "
                  + str(days) + " days, or " + str(years) + " years.")
        #  begin time loop
        for count in range(numsteps):
            # Compute the timestep
            self.step_forward()
            if count == 0:
                # on first step only...
                #  This implements a generic time-averaging feature
                # using the list of model state variables
                self.timeave = self.state.copy()
                # add any new diagnostics to the timeave dictionary
                self.timeave.update(self.diagnostics)
                # reset all values to zero
                for varname, value in self.timeave.iteritems():
                    self.timeave[varname] = 0*value
            # adding up all values for each timestep
            for varname in self.timeave.keys():
                try:
                    self.timeave[varname] += self.state[varname]
                except:
                    try:
                        self.timeave[varname] += self.diagnostics[varname]
                    except: pass
        # calculating mean values through dividing the sum by number of steps
        for varname in self.timeave.keys():
            self.timeave[varname] /= numsteps
        if verbose:
            print("Total elapsed time is %s years."
                  % str(self.time['days_elapsed']/const.days_per_year))

    def integrate_days(self, days=1.0, verbose=True):
        """Integrates the model forward for a specified number of days.
        
        It convertes the given number of days into years and calls 
        :func:`integrate_years`.
        
        :param days: integration time for the model in days
        :type years: float
        
        :param verbose: information whether model time details should be 
                        printed.
        :type verbose: boolean            
        
        """
        years = days / const.days_per_year
        self.integrate_years(years=years, verbose=verbose)
        
    def integrate_converge(self, crit=1e-4, verbose=True):
        """Integrates the model until model states are converging.
        
        :param crit: exit criteria for difference of iterated solutions
        :type crit: float
        
        :param verbose: information whether total elapsed time should be 
                        printed.
        :type verbose: boolean     
        
        """
        # implemented by m-kreuzer
        for varname, value in self.state.iteritems():
            value_old = copy.deepcopy(value)
            self.integrate_years(1,verbose=False)
            while np.max(np.abs(value_old-value)) > crit : 
                value_old = copy.deepcopy(value)                
                self.integrate_years(1,verbose=False)              
        if verbose == True:   
            print("Total elapsed time is %s years." 
                  % str(self.time['days_elapsed']/const.days_per_year))
            

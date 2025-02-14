import sys
import os
from importlib_metadata import version
from customized_utils import parse_fuzzing_arguments


sys.path.append('pymoo')
sys.path.append('fuzzing_utils')

fuzzing_arguments = parse_fuzzing_arguments()

if not fuzzing_arguments.debug:
    import warnings
    warnings.filterwarnings("ignore")

if fuzzing_arguments.simulator in ['carla', 'svl']:
    sys.path.append('..')
    carla_lbc_root = 'carla_lbc'
    sys.path.append(carla_lbc_root)
    sys.path.append(carla_lbc_root+'/leaderboard')
    sys.path.append(carla_lbc_root+'/leaderboard/team_code')
    sys.path.append(carla_lbc_root+'/scenario_runner')
    sys.path.append(carla_lbc_root+'/carla_project')
    sys.path.append(carla_lbc_root+'/carla_project/src')
    sys.path.append(carla_lbc_root+'/carla_specific_utils')

    if fuzzing_arguments.simulator in ['carla']:
        carla_root = os.path.expanduser('~/Documents/self-driving-cars/carla_0994_no_rss')
        sys.path.append(carla_root+'/PythonAPI/carla/dist/carla-0.9.9-py3.7-linux-x86_64.egg')
        sys.path.append(carla_root+'/PythonAPI/carla')
        sys.path.append(carla_root+'/PythonAPI')

        if version('carla') != '0.9.9':
            assert os.path.exists(carla_root+'/PythonAPI/carla/dist/carla-0.9.9-py3.7-linux-x86_64.egg')

            egg_path = os.path.join(carla_root, 'PythonAPI/carla/dist/carla-0.9.9-py3.7-linux-x86_64.egg')
            # os.system('pip uninstall carla')
            os.system('easy_install '+egg_path)


elif fuzzing_arguments.simulator in ['carla_op']:
    carla_root = os.path.expanduser('~/Documents/self-driving-cars/carla_0911_rss')
    if not os.path.exists(carla_root):
        carla_root = os.path.expanduser('~/Documents/self-driving-cars/carla_0911_no_rss')
    fuzzing_arguments.carla_path = os.path.join(carla_root, "CarlaUE4.sh")
    sys.path.append(carla_root+'/PythonAPI/carla/dist/carla-0.9.11-py3.7-linux-x86_64.egg')
    sys.path.append(carla_root+'/PythonAPI/carla')
    sys.path.append(carla_root+'/PythonAPI')

    # TBD: change to relative paths
    sys.path.append(os.path.expanduser('~/openpilot'))
    sys.path.append(os.path.expanduser('~/openpilot/tools/sim'))

    if version('carla') != '0.9.11':
        egg_path = os.path.join(carla_root, 'PythonAPI/carla/dist/carla-0.9.11-py3.7-linux-x86_64.egg')
        # os.system('pip uninstall carla')
        os.system('easy_install '+egg_path)




import json
import re
import time
import pathlib
import pickle
import copy
import atexit
import traceback
import math
from datetime import datetime
from distutils.dir_util import copy_tree

import numpy as np
from sklearn.preprocessing import StandardScaler
from scipy.stats import rankdata
from multiprocessing import Process, Manager, set_start_method


from pymoo.model.problem import Problem
from pymoo.model.crossover import Crossover
from pymoo.model.mutation import Mutation
from pymoo.model.population import Population
from pymoo.model.evaluator import Evaluator
from pymoo.algorithms.nsga2 import NSGA2, binary_tournament
from pymoo.operators.selection.tournament_selection import TournamentSelection
from pymoo.operators.crossover.simulated_binary_crossover import SimulatedBinaryCrossover

from pymoo.model.termination import Termination
from pymoo.util.termination.default import MultiObjectiveDefaultTermination, SingleObjectiveDefaultTermination
from pymoo.operators.mixed_variable_operator import MixedVariableMutation, MixedVariableCrossover
from pymoo.factory import get_crossover, get_mutation, get_termination


from pymoo.model.initialization import Initialization
from pymoo.model.duplicate import NoDuplicateElimination
from pymoo.model.survival import Survival
from pymoo.model.individual import Individual

# disable pymoo optimization warning
from pymoo.configuration import Configuration
Configuration.show_compile_hint = False

from pgd_attack import pgd_attack, train_net, train_regression_net, VanillaDataset
from acquisition import map_acquisition
from sampling import MySamplingVectorized, GridSampling, RandomDirectionSampling
from mating_and_repair import MyMatingVectorized, ClipRepair

from customized_utils import rand_real,  make_hierarchical_dir, exit_handler, is_critical_region, if_violate_constraints, filter_critical_regions, encode_fields, remove_fields_not_changing, get_labels_to_encode, customized_fit, customized_standardize, customized_inverse_standardize, decode_fields, encode_bounds, recover_fields_not_changing, process_X, inverse_process_X, calculate_rep_d, select_batch_max_d_greedy, if_violate_constraints_vectorized, is_distinct_vectorized, eliminate_repetitive_vectorized, get_sorted_subfolders, load_data, get_F, set_general_seed, emptyobject, get_job_results, choose_farthest_offs, torch_subset


# eliminate some randomness
set_general_seed(seed=fuzzing_arguments.random_seed)

def fun(obj, x, launch_server, counter, port, return_dict):
    dt = obj.dt
    estimator = obj.estimator
    critical_unique_leaves = obj.critical_unique_leaves
    customized_constraints = obj.customized_constraints
    labels = obj.labels

    default_objectives = obj.fuzzing_arguments.default_objectives
    run_simulation = obj.run_simulation
    fuzzing_content = obj.fuzzing_content
    fuzzing_arguments = obj.fuzzing_arguments
    sim_specific_arguments = obj.sim_specific_arguments
    dt_arguments = obj.dt_arguments


    not_critical_region = dt and not is_critical_region(x, estimator, critical_unique_leaves)
    violate_constraints, _ = if_violate_constraints(x, customized_constraints, labels, verbose=True)
    if not_critical_region or violate_constraints:
        returned_data = [default_objectives, None, 0]
    else:
        objectives, run_info  = run_simulation(x, fuzzing_content, fuzzing_arguments, sim_specific_arguments, dt_arguments, launch_server, counter, port)

        print('\n'*3)
        print("counter:", counter, " run_info['is_bug']:", run_info['is_bug'], " run_info['bug_type']:", run_info['bug_type'], " objectives:", objectives)
        print('\n'*3)

        # correct_travel_dist(x, labels, customized_data['tmp_travel_dist_file'])
        returned_data = [objectives, run_info, 1]
    if return_dict is not None:
        return_dict['returned_data'] = returned_data
    return returned_data


class MyProblem(Problem):

    def __init__(self, fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation, dt_arguments):

        self.fuzzing_arguments = fuzzing_arguments
        self.sim_specific_arguments = sim_specific_arguments
        self.fuzzing_content = fuzzing_content
        self.run_simulation = run_simulation
        self.dt_arguments = dt_arguments


        self.ego_car_model = fuzzing_arguments.ego_car_model
        #self.scheduler_port = fuzzing_arguments.scheduler_port
        #self.dashboard_address = fuzzing_arguments.dashboard_address
        self.ports = fuzzing_arguments.ports
        self.episode_max_time = fuzzing_arguments.episode_max_time
        self.objective_weights = fuzzing_arguments.objective_weights
        self.check_unique_coeff = fuzzing_arguments.check_unique_coeff
        self.consider_interested_bugs = fuzzing_arguments.consider_interested_bugs
        self.record_every_n_step = fuzzing_arguments.record_every_n_step
        self.use_single_objective = fuzzing_arguments.use_single_objective
        self.simulator = fuzzing_arguments.simulator


        if self.fuzzing_arguments.sample_avoid_ego_position and hasattr(self.sim_specific_arguments, 'ego_start_position'):
            self.ego_start_position = self.sim_specific_arguments.ego_start_position
        else:
            self.ego_start_position = None


        self.call_from_dt = dt_arguments.call_from_dt
        self.dt = dt_arguments.dt
        self.estimator = dt_arguments.estimator
        self.critical_unique_leaves = dt_arguments.critical_unique_leaves
        self.cumulative_info = dt_arguments.cumulative_info
        cumulative_info = dt_arguments.cumulative_info

        if cumulative_info:
            self.counter = cumulative_info['counter']
            self.has_run = cumulative_info['has_run']
            self.start_time = cumulative_info['start_time']
            self.time_list = cumulative_info['time_list']
            self.bugs = cumulative_info['bugs']
            self.unique_bugs = cumulative_info['unique_bugs']
            self.interested_unique_bugs = cumulative_info['interested_unique_bugs']
            self.bugs_type_list = cumulative_info['bugs_type_list']
            self.bugs_inds_list = cumulative_info['bugs_inds_list']
            self.bugs_num_list = cumulative_info['bugs_num_list']
            self.unique_bugs_num_list = cumulative_info['unique_bugs_num_list']
            self.has_run_list = cumulative_info['has_run_list']
        else:
            self.counter = 0
            self.has_run = 0
            self.start_time = time.time()
            self.time_list = []
            self.bugs = []
            self.unique_bugs = []
            self.interested_unique_bugs = []
            self.bugs_type_list = []
            self.bugs_inds_list = []
            self.bugs_num_list = []
            self.unique_bugs_num_list = []
            self.has_run_list = []


        self.labels = fuzzing_content.labels
        self.mask = fuzzing_content.mask
        self.parameters_min_bounds = fuzzing_content.parameters_min_bounds
        self.parameters_max_bounds = fuzzing_content.parameters_max_bounds
        self.parameters_distributions = fuzzing_content.parameters_distributions
        self.customized_constraints = fuzzing_content.customized_constraints
        self.customized_center_transforms = fuzzing_content.customized_center_transforms
        xl = [pair[1] for pair in self.parameters_min_bounds.items()]
        xu = [pair[1] for pair in self.parameters_max_bounds.items()]
        n_var = fuzzing_content.n_var


        self.p, self.c, self.th = self.check_unique_coeff
        self.launch_server = True
        self.objectives_list = []
        self.trajectory_vector_list = []
        self.x_list = []
        self.y_list = []
        self.F_list = []


        super().__init__(n_var=n_var, n_obj=4, n_constr=0, xl=xl, xu=xu)




    def _evaluate(self, X, out, *args, **kwargs):
        objective_weights = self.objective_weights
        customized_center_transforms = self.customized_center_transforms

        episode_max_time = self.episode_max_time

        default_objectives = self.fuzzing_arguments.default_objectives
        standardize_objective = self.fuzzing_arguments.standardize_objective
        normalize_objective = self.fuzzing_arguments.normalize_objective
        traj_dist_metric = self.fuzzing_arguments.traj_dist_metric


        all_final_generated_transforms_list = []

        # non-dask subprocess implementation
        # rng = np.random.default_rng(random_seeds[1])

        tmp_run_info_list = []
        x_sublist = []
        objectives_sublist_non_traj = []
        trajectory_vector_sublist = []

        for i in range(X.shape[0]):
            if self.counter == 0:
                launch_server = True
            else:
                launch_server = False
            cur_i = i
            total_i = self.counter

            port = self.ports[0]
            x = X[cur_i]

            # No need to use subprocess when no simulation is running
            if self.fuzzing_arguments.simulator in ['no_simulation_dataset', 'no_simulation_function']:
                return_dict = {}
                fun(self, x, launch_server, self.counter, port, return_dict)
            else:
                manager = Manager()
                return_dict = manager.dict()
                try:
                    p = Process(target=fun, args=(self, x, launch_server, self.counter, port, return_dict))
                    p.start()
                    p.join(240)
                    if p.is_alive():
                        print("Function is hanging!")
                        p.terminate()
                        print("Kidding, just terminated!")
                except:
                    traceback.print_exc()
                    objectives, run_info, has_run = default_objectives, None, 0

            if 'returned_data' in return_dict:
                objectives, run_info, has_run = return_dict['returned_data']
            else:
                # TBD: add an error log
                print('\n'*3, 'returned_data is missing', '\n'*3)
                objectives, run_info, has_run = default_objectives, None, 0

            print('get job result for', total_i)
            if run_info and 'all_final_generated_transforms' in run_info:
                all_final_generated_transforms_list.append(run_info['all_final_generated_transforms'])

            self.has_run_list.append(has_run)
            self.has_run += has_run

            # record bug
            if run_info and run_info['is_bug']:
                self.bugs.append(X[cur_i].astype(float))
                self.bugs_inds_list.append(total_i)
                self.bugs_type_list.append(run_info['bug_type'])

                self.y_list.append(run_info['bug_type'])
            else:
                self.y_list.append(0)



            self.counter += 1
            tmp_run_info_list.append(run_info)
            x_sublist.append(x)
            objectives_sublist_non_traj.append(objectives)
            if run_info and 'trajectory_vector' in run_info:
                trajectory_vector_sublist.append(run_info['trajectory_vector'])
            else:
                trajectory_vector_sublist.append(None)


        job_results, self.x_list, self.objectives_list, self.trajectory_vector_list = get_job_results(tmp_run_info_list, x_sublist, objectives_sublist_non_traj, trajectory_vector_sublist, self.x_list, self.objectives_list, self.trajectory_vector_list, traj_dist_metric)
        # print('self.objectives_list', self.objectives_list)


        # hack:
        if run_info and 'all_final_generated_transforms' in run_info:
            with open('carla_lbc/tmp_folder/total.pickle', 'wb') as f_out:
                pickle.dump(all_final_generated_transforms_list, f_out)

        # record time elapsed and bug numbers
        time_elapsed = time.time() - self.start_time
        self.time_list.append(time_elapsed)


        current_F = get_F(job_results, self.objectives_list, objective_weights, self.use_single_objective, standardize=standardize_objective, normalize=normalize_objective)

        out["F"] = current_F
        self.F_list.append(current_F)
        # print('\n'*3, 'self.F_list', len(self.F_list), self.F_list, '\n'*3)
        print('\n'*10, '+'*100)


        bugs_type_list_tmp = self.bugs_type_list
        bugs_tmp = self.bugs
        bugs_inds_list_tmp = self.bugs_inds_list

        self.unique_bugs, unique_bugs_inds_list, self.interested_unique_bugs, bugcounts = get_unique_bugs(self.x_list, self.objectives_list, self.mask, self.xl, self.xu, self.check_unique_coeff, objective_weights, return_mode='unique_inds_and_interested_and_bugcounts', consider_interested_bugs=1, bugs_type_list=bugs_type_list_tmp, bugs=bugs_tmp, bugs_inds_list=bugs_inds_list_tmp, trajectory_vector_list=self.trajectory_vector_list)


        time_elapsed = time.time() - self.start_time
        num_of_bugs = len(self.bugs)
        num_of_unique_bugs = len(self.unique_bugs)
        num_of_interested_unique_bugs = len(self.interested_unique_bugs)

        self.bugs_num_list.append(num_of_bugs)
        self.unique_bugs_num_list.append(num_of_unique_bugs)
        mean_objectives_this_generation = np.mean(np.array(self.objectives_list[-X.shape[0]:]), axis=0)

        with open(self.fuzzing_arguments.mean_objectives_across_generations_path, 'a') as f_out:

            info_dict = {
                'counter': self.counter,
                'has_run': self.has_run,
                'time_elapsed': time_elapsed,
                'num_of_bugs': num_of_bugs,
                'num_of_unique_bugs': num_of_unique_bugs,
                'num_of_interested_unique_bugs': num_of_interested_unique_bugs,
                'bugcounts and unique bug counts': bugcounts, 'mean_objectives_this_generation': mean_objectives_this_generation.tolist(),
                'current_F': current_F
            }

            f_out.write(str(info_dict))
            f_out.write(';'.join([str(ind) for ind in unique_bugs_inds_list])+' objective_weights : '+str(self.objective_weights)+'\n')
        print(info_dict)

        print('+'*100, '\n'*10)


def do_emcmc(parents, off, n_gen, objective_weights, default_objectives):
    base_val = np.sum(np.array(default_objectives[:len(objective_weights)])*np.array(objective_weights))
    filtered_off = []
    F_list = []
    for i in off:
        for p in parents:
            print(i.F, p.F)

            i_val = np.sum(np.array(i.F) * np.array(objective_weights))
            p_val = np.sum(np.array(p.F) * np.array(objective_weights))

            print('1', base_val, i_val, p_val)
            i_val = np.abs(base_val-i_val)
            p_val = np.abs(base_val-p_val)
            prob = np.min([i_val / p_val, 1])
            print('2', base_val, i_val, p_val, prob)

            if np.random.uniform() < prob:
                filtered_off.append(i.X)
                F_list.append(i.F)

    pop = Population(len(filtered_off), individual=Individual())
    pop.set("X", filtered_off, "F", F_list, "n_gen", n_gen, "CV", [0 for _ in range(len(filtered_off))], "feasible", [[True] for _ in range(len(filtered_off))])

    return Population.merge(parents, off)






class NSGA2_CUSTOMIZED(NSGA2):
    def __init__(self, dt=False, X=None, F=None, fuzzing_arguments=None, random_sampling=None, local_mating=None, **kwargs):
        self.dt = dt
        self.X = X
        self.F = F
        self.random_sampling = random_sampling

        self.sampling = kwargs['sampling']
        self.pop_size = fuzzing_arguments.pop_size
        self.n_offsprings = fuzzing_arguments.n_offsprings

        self.survival_multiplier = fuzzing_arguments.survival_multiplier
        self.algorithm_name = fuzzing_arguments.algorithm_name
        self.emcmc = fuzzing_arguments.emcmc
        self.initial_fit_th = fuzzing_arguments.initial_fit_th
        self.rank_mode = fuzzing_arguments.rank_mode
        self.min_bug_num_to_fit_dnn = fuzzing_arguments.min_bug_num_to_fit_dnn
        self.ranking_model = fuzzing_arguments.ranking_model
        self.use_unique_bugs = fuzzing_arguments.use_unique_bugs
        self.pgd_eps = fuzzing_arguments.pgd_eps
        self.adv_conf_th = fuzzing_arguments.adv_conf_th
        self.attack_stop_conf = fuzzing_arguments.attack_stop_conf
        self.uncertainty = fuzzing_arguments.uncertainty
        self.warm_up_path = fuzzing_arguments.warm_up_path
        self.warm_up_len = fuzzing_arguments.warm_up_len
        self.regression_nn_use_running_data = fuzzing_arguments.regression_nn_use_running_data
        self.only_run_unique_cases = fuzzing_arguments.only_run_unique_cases

        super().__init__(pop_size=self.pop_size, n_offsprings=self.n_offsprings, **kwargs)

        self.random_initialization = Initialization(self.random_sampling, individual=Individual(), repair=self.repair, eliminate_duplicates= NoDuplicateElimination())


        # heuristic: we keep up about 1 times of each generation's population
        self.survival_size = self.pop_size * self.survival_multiplier

        self.all_pop_run_X = []

        # hack: defined separately w.r.t. MyMating
        self.mating_max_iterations = 1

        self.tmp_off = []
        self.tmp_off_type_1_len = 0
        # self.tmp_off_type_1and2_len = 0

        self.high_conf_configs_stack = []
        self.high_conf_configs_ori_stack = []

        self.device_name = 'cuda'


        # avfuzzer variables
        self.best_y_gen = []
        self.global_best_y = [None, 10000]
        self.restart_best_y = [None, 10000]
        self.local_best_y = [None, 10000]

        self.pop_before_local = None

        self.local_gen = -1
        self.restart_gen = 0
        self.cur_gen = -1

        self.local_mating = local_mating
        self.mutation = kwargs['mutation']

        self.minLisGen = 2




    def set_off(self):
        self.tmp_off = []

        if self.algorithm_name == 'avfuzzer':
            cur_best_y = [None, 10000]
            if self.cur_gen >= 0:
                # local search
                if 0 <= self.local_gen <= 4:
                    with open('tmp_log.txt', 'a') as f_out:
                        f_out.write(str(self.cur_gen)+' local '+str(self.local_gen)+'\n')

                    cur_pop = self.pop[-self.pop_size:]
                    for p in cur_pop:
                        if p.F < self.local_best_y[1]:
                            self.local_best_y = [p, p.F]
                    if self.local_gen == 4:
                        self.local_gen = -1
                        if self.local_best_y[1] < self.global_best_y[1]:
                            self.global_best_y = self.local_best_y
                        if self.local_best_y[1] < self.best_y_gen[-1][1]:
                            self.best_y_gen[-1] = self.local_best_y
                        # if self.local_best_y[1] < self.restart_best_y[1]:
                        #     self.restart_best_y = self.local_best_y

                        tmp_best_ind = 0
                        tmp_best_y = [None, 10000]
                        for i, p in enumerate(self.pop_before_local):
                            if p.F < tmp_best_y[1]:
                                tmp_best_y = [p, p.F]
                                tmp_best_ind = i

                        self.pop_before_local[tmp_best_ind] = self.local_best_y[0]
                        self.tmp_off, _ = self.mating.do(self.problem, self.pop_before_local, self.n_offsprings, algorithm=self)

                        self.cur_gen += 1
                    else:
                        self.local_gen += 1

                        self.tmp_off, _ = self.local_mating.do(self.problem, self.pop, self.n_offsprings, algorithm=self)

                # global search
                else:
                    cur_pop = self.pop[-self.pop_size:]
                    for p in cur_pop:
                        if p.F < cur_best_y[1]:
                            cur_best_y = [p, p.F]
                    if cur_best_y[1] < self.global_best_y[1]:
                        self.global_best_y = cur_best_y
                    if len(self.best_y_gen) == self.cur_gen:
                        self.best_y_gen.append(cur_best_y)
                    else:
                        if cur_best_y[1] < self.best_y_gen[-1][1]:
                            self.best_y_gen[-1] = cur_best_y

                    if self.cur_gen - self.restart_gen <= self.minLisGen:
                        if cur_best_y[1] < self.restart_best_y[1]:
                            self.restart_best_y = cur_best_y


                    with open('tmp_log.txt', 'a') as f_out:
                        f_out.write('self.global_best_y: '+ str(self.global_best_y[1])+', cur_best_y[1]: '+str(cur_best_y[1])+', self.restart_best_y[1]: '+str(self.restart_best_y[1])+'\n')

                    normal = True
                    # restart
                    if self.cur_gen - self.restart_gen > 4:
                        last_5_mean = np.mean([v for _, v in self.best_y_gen[-5:]])

                        with open('tmp_log.txt', 'a') as f_out:
                            f_out.write('last_5_mean: '+str(last_5_mean)+', cur_best_y[1]: '+str(cur_best_y[1])+'\n')
                        if cur_best_y[1] >= last_5_mean:
                            with open('tmp_log.txt', 'a') as f_out:
                                f_out.write(str(self.cur_gen)+' restart'+'\n')

                            tmp_off_candidates = self.random_initialization.do(self.problem, 1000, algorithm=self)
                            tmp_off_candidates_X = np.stack([p.X for p in tmp_off_candidates])
                            chosen_inds = choose_farthest_offs(tmp_off_candidates_X, self.all_pop_run_X, self.pop_size)
                            self.tmp_off = tmp_off_candidates[chosen_inds]
                            self.restart_best_y = [None, 10000]
                            normal = False
                            self.cur_gen += 1
                            self.restart_gen = self.cur_gen


                    # enter local
                    if normal and self.cur_gen - self.restart_gen > self.minLisGen and cur_best_y[1] < self.restart_best_y[1]:
                            with open('tmp_log.txt', 'a') as f_out:
                                f_out.write(str(self.cur_gen)+'enter local'+'\n')
                            self.restart_best_y[1] = cur_best_y[1]
                            self.pop_before_local = copy.deepcopy(self.pop)
                            pop = Population(self.pop_size, individual=Individual())
                            pop.set("X", [self.global_best_y[0].X for _ in range(self.pop_size)])
                            pop.set("F", [self.global_best_y[1] for _ in range(self.pop_size)])
                            self.tmp_off = self.mutation.do(self.problem, pop)

                            self.local_best_y = [None, 10000]
                            self.local_gen = 0
                            normal = False
                            # not increasing cur_gen in this case
                    if normal:
                        with open('tmp_log.txt', 'a') as f_out:
                            f_out.write(str(self.cur_gen)+' normal'+'\n')
                        self.tmp_off, _ = self.mating.do(self.problem, self.pop, self.pop_size, algorithm=self)
                        self.cur_gen += 1
            else:
                # initialization
                self.tmp_off = self.random_initialization.do(self.problem, self.n_offsprings, algorithm=self)
                self.cur_gen += 1


        elif self.algorithm_name in ['random', 'grid']:
            self.tmp_off = self.initialization.do(self.problem, self.n_offsprings, algorithm=self)
        # elif self.algorithm_name in ['random_local_sphere']:
        #     # print('self.sampling.cur_ind', self.sampling.cur_ind)
        #     # if self.sampling.cur_ind > -1:
        #     #     print('self.sampling.spheres[self.sampling.cur_ind].sampling_num', self.sampling.spheres[self.sampling.cur_ind].sampling_num)
        #     if len(self.sampling.spheres) > 0 and self.sampling.spheres[self.sampling.cur_ind].if_local_sampling():
        #         latest_ind, latest_x, latest_y = len(self.problem.x_list)-1, self.problem.x_list[-1], self.problem.y_list[-1]
        #         self.sampling.update_cur_sphere(latest_ind, latest_x, latest_y)
        #
        #     if len(self.sampling.spheres) == 0 or not self.sampling.spheres[self.sampling.cur_ind].if_local_sampling():
        #         self.sampling.add_uncovered_coverable_bugs(self.problem.x_list, self.problem.y_list)
        #         uncovered_bug = self.sampling.find_an_uncovered_bug(self.problem.x_list, self.problem.y_list)
        #         # If an uncovered bug is found by global sampling
        #         if uncovered_bug:
        #             self.sampling.new_sphere(uncovered_bug, self.problem.x_list, self.problem.y_list)
        #             tmp_val = self.sampling._do(self.problem, self.n_offsprings)
        #             pop = Population(0, individual=Individual())
        #             self.tmp_off = pop.new("X", tmp_val)
        #         # do global sampling when no available bug can be used as a new center
        #         else:
        #             offspring_multiplier = 1000
        #             sphere_center_d_th_random_sampling = 0.1
        #
        #             tmp_x_list = self.random_sampling._do(self.problem, self.n_offsprings*offspring_multiplier, algorithm=self)
        #             d_list = self.sampling.d_to_spheres(tmp_x_list)
        #
        #             candidate_x_list_inds = np.where(d_list > sphere_center_d_th_random_sampling)[0]
        #             if len(candidate_x_list_inds) < self.n_offsprings:
        #                 candidate_x_list_inds = np.argsort(d_list)[-self.n_offsprings:]
        #                 tmp_val = np.array(tmp_x_list)[candidate_x_list_inds]
        #             else:
        #                 tmp_val = np.random.choice(candidate_x_list_inds, size=self.n_offsprings, replace=False)
        #                 tmp_val = np.array(tmp_x_list)[candidate_x_list_inds]
        #             pop = Population(0, individual=Individual())
        #             self.tmp_off = pop.new("X", tmp_val)
        #     else:
        #         tmp_val = self.sampling._do(self.problem, self.n_offsprings)
        #         pop = Population(0, individual=Individual())
        #         self.tmp_off = pop.new("X", tmp_val)

        else:
            if self.algorithm_name == 'random-un':
                self.tmp_off, parents = [], []
            else:
                print('len(self.pop)', len(self.pop))
                # do the mating using the current population
                if len(self.pop) > 0:
                    self.tmp_off, parents = self.mating.do(self.problem, self.pop, self.n_offsprings, algorithm=self)

            print('\n'*3, 'after mating len 0', len(self.tmp_off), 'self.n_offsprings', self.n_offsprings, '\n'*3)

            if len(self.tmp_off) < self.n_offsprings:
                remaining_num = self.n_offsprings - len(self.tmp_off)
                remaining_off = self.initialization.do(self.problem, remaining_num, algorithm=self)
                remaining_parrents = remaining_off
                if len(self.tmp_off) == 0:
                    self.tmp_off = remaining_off
                    parents = remaining_parrents
                else:
                    self.tmp_off = Population.merge(self.tmp_off, remaining_off)
                    parents = Population.merge(parents, remaining_parrents)

                print('\n'*3, 'unique after random generation len 1', len(self.tmp_off), '\n'*3)

            self.tmp_off_type_1_len = len(self.tmp_off)

            if len(self.tmp_off) < self.n_offsprings:
                remaining_num = self.n_offsprings - len(self.tmp_off)
                remaining_off = self.random_initialization.do(self.problem, remaining_num, algorithm=self)
                remaining_parrents = remaining_off

                self.tmp_off = Population.merge(self.tmp_off, remaining_off)
                parents = Population.merge(parents, remaining_parrents)

                print('\n'*3, 'random generation len 2', len(self.tmp_off), '\n'*3)


        # if the mating could not generate any new offspring (duplicate elimination might make that happen)
        no_offspring = len(self.tmp_off) == 0
        not_nsga2_dt_and_finish_has_run = not self.problem.call_from_dt and self.problem.fuzzing_arguments.finish_after_has_run and self.problem.has_run >= self.problem.fuzzing_arguments.has_run_num
        if no_offspring or not_nsga2_dt_and_finish_has_run:
            self.termination.force_termination = True
            print("Mating cannot generate new springs, terminate earlier.")
            print('self.tmp_off', len(self.tmp_off))
            return
        # if not the desired number of offspring could be created
        elif len(self.tmp_off) < self.n_offsprings:
            if self.verbose:
                print("WARNING: Mating could not produce the required number of (unique) offsprings!")


        # additional step to rank and select self.off after gathering initial population
        no_ranking = self.rank_mode == 'none'
        cla_nn_ranking_and_no_enough_samples_or_no_enough_bugs = self.rank_mode in ['nn', 'adv_nn'] and (len(self.problem.objectives_list) < self.initial_fit_th or  np.sum(determine_y_upon_weights(self.problem.objectives_list, self.problem.objective_weights)) < self.min_bug_num_to_fit_dnn)
        reg_ranking_and_no_enough_samples = self.rank_mode in ['regression_nn'] and len(self.problem.objectives_list) < self.pop_size

        if no_ranking or cla_nn_ranking_and_no_enough_samples_or_no_enough_bugs or reg_ranking_and_no_enough_samples:
            self.off = self.tmp_off[:self.pop_size]
        else:
            if self.rank_mode in ['regression_nn']:
                from customized_utils import pretrain_regression_nets

                if self.regression_nn_use_running_data:
                    initial_X = self.all_pop_run_X
                    initial_objectives_list = self.problem.objectives_list
                    cutoff = len(initial_X)
                    cutoff_end = cutoff
                else:
                    subfolders = get_sorted_subfolders(self.warm_up_path)
                    initial_X, _, initial_objectives_list, _, _, _ = load_data(subfolders)

                    cutoff = self.warm_up_len
                    cutoff_end = self.warm_up_len + 100

                    if cutoff == 0:
                        cutoff = len(initial_X)
                    if cutoff_end > len(initial_X):
                        cutoff_end = len(initial_X)

                clfs, confs, chosen_weights, standardize_prev = pretrain_regression_nets(initial_X, initial_objectives_list, self.problem.objective_weights, self.problem.xl, self.problem.xu, self.problem.labels, self.problem.customized_constraints, cutoff, cutoff_end, self.problem.fuzzing_content.keywords_dict, choose_weight_inds)
            else:
                standardize_prev = None

            X_train_ori = self.all_pop_run_X
            X_test_ori = self.tmp_off.get("X")

            initial_X = np.concatenate([X_train_ori, X_test_ori])
            cutoff = X_train_ori.shape[0]
            cutoff_end = initial_X.shape[0]
            partial = True

            X_train, X_test, xl, xu, labels_used, standardize, one_hot_fields_len, param_for_recover_and_decode = process_X(initial_X, self.problem.labels, self.problem.xl, self.problem.xu, cutoff, cutoff_end, partial, len(self.problem.interested_unique_bugs), self.problem.fuzzing_content.keywords_dict, standardize_prev=standardize_prev)

            (X_removed, kept_fields, removed_fields, enc, inds_to_encode, inds_non_encode, encoded_fields, _, _, unique_bugs_len) = param_for_recover_and_decode

            print('process_X finished')
            if self.rank_mode in ['regression_nn']:
                weight_inds = choose_weight_inds(self.problem.objective_weights)
                obj_preds = []
                for clf in clfs:
                    obj_preds.append(clf.predict(X_test))

                tmp_objectives = np.concatenate(obj_preds, axis=1)

                if self.use_unique_bugs:
                    tmp_objectives[:self.tmp_off_type_1_len] -= 100*chosen_weights


                tmp_objectives_minus = tmp_objectives - confs
                tmp_objectives_plus = tmp_objectives + confs

                tmp_pop_minus = Population(X_train.shape[0]+X_test.shape[0], individual=Individual())
                tmp_X_minus = np.concatenate([X_train, X_test])

                tmp_objectives_minus = np.concatenate([np.array(self.problem.objectives_list)[:, weight_inds], tmp_objectives_minus]) * np.array(self.problem.objective_weights[weight_inds])

                tmp_pop_minus.set("X", tmp_X_minus)
                tmp_pop_minus.set("F", tmp_objectives_minus)

                print('len(tmp_objectives_minus)', len(tmp_objectives_minus))
                inds_minus_top = np.array(self.survival.do(self.problem, tmp_pop_minus, self.pop_size, return_indices=True))
                print('inds_minus_top', inds_minus_top, 'len(X_train)', len(X_train), np.sum(inds_minus_top<len(X_train)))

                num_of_top_already_run = np.sum(inds_minus_top<len(X_train))
                num_to_run = self.pop_size - num_of_top_already_run

                if num_to_run > 0:
                    tmp_pop_plus = Population(X_test.shape[0], individual=Individual())

                    tmp_X_plus = X_test
                    tmp_objectives_plus = tmp_objectives_plus * np.array(self.problem.objective_weights[weight_inds])

                    tmp_pop_plus.set("X", tmp_X_plus)
                    tmp_pop_plus.set("F", tmp_objectives_plus)

                    print('tmp_objectives_plus', tmp_objectives_plus)
                    inds_plus_top = np.array(self.survival.do(self.problem, tmp_pop_plus, num_to_run, return_indices=True))

                    print('inds_plus_top', inds_plus_top)
                    self.off = self.tmp_off[inds_plus_top]
                else:
                    print('no more offsprings to run (regression nn)')
                    self.off = Population(0, individual=Individual())
            else:
                # ---seed selection---
                if self.uncertainty:
                    y_train = determine_y_upon_weights(self.problem.objectives_list, self.problem.objective_weights)

                    print('uncertainty', self.uncertainty)
                    if self.uncertainty == 'nndv':
                        from customized_utils import nndv
                        # TBD: make the following can be adjusted from the interface
                        angle_features = [2]
                        scales = [2, 2, 15]
                        inds = nndv(X_train, y_train, X_test, self.pop_size, angle_features, scales)
                    else:
                        uncertainty_key, uncertainty_conf = self.uncertainty.split('_')

                        acquisition_strategy = map_acquisition(uncertainty_key)
                        acquirer = acquisition_strategy(self.pop_size)

                        if uncertainty_conf == 'conf':
                            uncertainty_conf = True
                        else:
                            uncertainty_conf = False

                        pool_data = torch_subset(VanillaDataset(X_test, np.zeros(X_test.shape[0]), to_tensor=True))

                        clf = train_net(X_train, y_train, [], [], batch_train=60, device_name=self.device_name)

                        if self.use_unique_bugs:
                            unique_len = self.tmp_off_type_1_len
                        else:
                            unique_len = 0
                        inds = acquirer.select_batch(clf, pool_data, unique_len=unique_len, uncertainty_conf=uncertainty_conf)

                else:
                    adv_conf_th = self.adv_conf_th
                    attack_stop_conf = self.attack_stop_conf

                    y_train = determine_y_upon_weights(self.problem.objectives_list, self.problem.objective_weights)

                    if self.ranking_model == 'nn_pytorch':
                        print(X_train.shape, y_train.shape)
                        clf = train_net(X_train, y_train, [], [], batch_train=200, device_name=self.device_name)
                    elif self.ranking_model == 'adaboost':
                        from sklearn.ensemble import AdaBoostClassifier
                        clf = AdaBoostClassifier()
                        clf = clf.fit(X_train, y_train)
                    else:
                        raise ValueError('invalid ranking model', ranking_model)
                    print('X_train', X_train.shape)
                    print('clf.predict_proba(X_train)', clf.predict_proba(X_train).shape)
                    if self.ranking_model == 'adaboost':
                        prob_train = clf.predict_proba(X_train)[:, 0].squeeze()
                    else:
                        prob_train = clf.predict_proba(X_train)[:, 1].squeeze()
                    cur_y = y_train

                    if self.adv_conf_th < 0 and self.rank_mode in ['adv_nn']:
                        # print(sorted(prob_train, reverse=True))
                        # print('cur_y', cur_y)
                        # print('np.abs(self.adv_conf_th)', np.abs(self.adv_conf_th))
                        # print(int(np.sum(cur_y)//np.abs(self.adv_conf_th)))
                        adv_conf_th = sorted(prob_train, reverse=True)[int(np.sum(cur_y)//np.abs(self.adv_conf_th))]
                        attack_stop_conf = np.max([self.attack_stop_conf, adv_conf_th])
                    if self.adv_conf_th > attack_stop_conf:
                        self.adv_conf_th = attack_stop_conf


                    pred = clf.predict_proba(X_test)
                    if len(pred.shape) == 1:
                        pred = np.expand_dims(pred, axis=0)
                    scores = pred[:, 1]

                    print('initial scores', scores)
                    # when using unique bugs give preference to unique inputs

                    if self.rank_mode == 'adv_nn':
                        X_test_pgd_ori = None
                        X_test_pgd = None


                    if self.use_unique_bugs:
                        print('self.tmp_off_type_1_len', self.tmp_off_type_1_len)
                        scores[:self.tmp_off_type_1_len] += np.max(scores)
                        # scores[:self.tmp_off_type_1and2_len] += 100
                    scores *= -1

                    inds = np.argsort(scores)[:self.pop_size]
                    print('scores', scores)
                    print('sorted(scores)', sorted(scores))
                    print('chosen indices', inds)

                # ---additional mutation on selected seeds---
                if self.rank_mode == 'nn':
                    self.off = self.tmp_off[inds]
                elif self.rank_mode == 'adv_nn':
                    X_test_pgd_ori = X_test_ori[inds]
                    X_test_pgd = X_test[inds]
                    associated_clf_id = []

                    # conduct pgd with constraints differently for different types of inputs
                    if self.use_unique_bugs:
                        unique_coeff = (self.problem.p, self.problem.c, self.problem.th)
                        mask = self.problem.mask

                        y_zeros = np.zeros(X_test_pgd.shape[0])
                        X_test_adv, new_bug_pred_prob_list, initial_bug_pred_prob_list = pgd_attack(clf, X_test_pgd, y_zeros, xl, xu, encoded_fields, labels_used, self.problem.customized_constraints, standardize, prev_X=self.problem.interested_unique_bugs, base_ind=0, unique_coeff=unique_coeff, mask=mask, param_for_recover_and_decode=param_for_recover_and_decode, eps=self.pgd_eps, adv_conf_th=adv_conf_th, attack_stop_conf=attack_stop_conf, associated_clf_id=associated_clf_id, X_test_pgd_ori=X_test_pgd_ori, consider_uniqueness=True, device_name=self.device_name)

                    else:
                        y_zeros = np.zeros(X_test_pgd.shape[0])
                        X_test_adv, new_bug_pred_prob_list, initial_bug_pred_prob_list = pgd_attack(clf, X_test_pgd, y_zeros, xl, xu, encoded_fields, labels_used, self.problem.customized_constraints, standardize, eps=self.pgd_eps, adv_conf_th=adv_conf_th, attack_stop_conf=attack_stop_conf, associated_clf_id=associated_clf_id, X_test_pgd_ori=X_test_pgd_ori, device_name=self.device_name)

                    X_test_adv_processed = inverse_process_X(X_test_adv, standardize, one_hot_fields_len, partial, X_removed, kept_fields, removed_fields, enc, inds_to_encode, inds_non_encode, encoded_fields)
                    X_off = X_test_adv_processed

                    pop = Population(X_off.shape[0], individual=Individual())
                    pop.set("X", X_off)
                    pop.set("F", [None for _ in range(X_off.shape[0])])
                    self.off = pop


        if self.only_run_unique_cases:
            X_off = [off_i.X for off_i in self.off]
            remaining_inds = is_distinct_vectorized(X_off, self.problem.interested_unique_bugs, self.problem.mask, self.problem.xl, self.problem.xu, self.problem.p, self.problem.c, self.problem.th, verbose=False)
            self.off = self.off[remaining_inds]

        self.off.set("n_gen", self.n_gen)

        print('\n'*2, 'self.n_gen', self.n_gen, '\n'*2)

        if len(self.all_pop_run_X) == 0:
            self.all_pop_run_X = self.off.get("X")
        else:
            if len(self.off.get("X")) > 0:
                self.all_pop_run_X = np.concatenate([self.all_pop_run_X, self.off.get("X")])

    # mainly used to modify survival
    def _next(self):

        # set self.off
        self.set_off()
        # evaluate the offspring
        if len(self.off) > 0:
            self.evaluator.eval(self.problem, self.off, algorithm=self)


        if self.algorithm_name in ['random', 'avfuzzer', 'grid', 'random_local_sphere']:
            self.pop = self.off
        elif self.emcmc:
            new_pop = do_emcmc(parents, self.off, self.n_gen, self.problem.objective_weights, self.problem.fuzzing_arguments.default_objectives)

            self.pop = Population.merge(self.pop, new_pop)

            if self.survival:
                self.pop = self.survival.do(self.problem, self.pop, self.survival_size, algorithm=self, n_min_infeas_survive=self.min_infeas_pop_size)
        else:
            # merge the offsprings with the current population
            self.pop = Population.merge(self.pop, self.off)

            # the do survival selection
            if self.survival:
                print('\n'*3)
                print('len(self.pop) before', len(self.pop))
                print('survival')
                self.pop = self.survival.do(self.problem, self.pop, self.survival_size, algorithm=self, n_min_infeas_survive=self.min_infeas_pop_size)
                print('len(self.pop) after', len(self.pop))
                print(self.pop_size, self.survival_size)
                print('\n'*3)



    def _initialize(self):
        if self.warm_up_path and ((self.dt and not self.problem.cumulative_info) or (not self.dt)):
            subfolders = get_sorted_subfolders(self.warm_up_path)
            X, _, objectives_list, mask, _, _ = load_data(subfolders)

            if self.warm_up_len > 0:
                X = X[:self.warm_up_len]
                objectives_list = objectives_list[:self.warm_up_len]
            else:
                self.warm_up_len = len(X)

            xl = self.problem.xl
            xu = self.problem.xu
            p, c, th = self.problem.p, self.problem.c, self.problem.th
            unique_coeff = (p, c, th)


            self.problem.unique_bugs, (self.problem.bugs, self.problem.bugs_type_list, self.problem.bugs_inds_list, self.problem.interested_unique_bugs) = get_unique_bugs(
                X, objectives_list, mask, xl, xu, unique_coeff, self.problem.objective_weights, return_mode='return_bug_info', consider_interested_bugs=self.problem.consider_interested_bugs
            )

            print('\n'*10)
            print('self.problem.bugs', len(self.problem.bugs))
            print('self.problem.unique_bugs', len(self.problem.unique_bugs))
            print('\n'*10)

            self.all_pop_run_X = np.array(X)
            self.problem.objectives_list = objectives_list.tolist()

        if self.dt:
            X_list = list(self.X)
            F_list = list(self.F)
            pop = Population(len(X_list), individual=Individual())
            pop.set("X", X_list, "F", F_list, "n_gen", self.n_gen, "CV", [0 for _ in range(len(X_list))], "feasible", [[True] for _ in range(len(X_list))])
            self.pop = pop
            self.set_off()
            pop = self.off

        elif self.warm_up_path:
            X_list = X[-self.pop_size:]
            current_objectives = objectives_list[-self.pop_size:]


            F_list = get_F(current_objectives, objectives_list, self.problem.objective_weights, self.problem.use_single_objective)


            pop = Population(len(X_list), individual=Individual())
            pop.set("X", X_list, "F", F_list, "n_gen", self.n_gen, "CV", [0 for _ in range(len(X_list))], "feasible", [[True] for _ in range(len(X_list))])

            self.pop = pop
            self.set_off()
            pop = self.off

        else:
            # create the initial population
            if self.use_unique_bugs:
                pop = self.initialization.do(self.problem, self.problem.fuzzing_arguments.pop_size, algorithm=self)
            else:
                pop = self.random_initialization.do(self.problem, self.pop_size, algorithm=self)
            pop.set("n_gen", self.n_gen)


        if len(pop) > 0:
            self.evaluator.eval(self.problem, pop, algorithm=self)
        print('\n'*5, 'after initialize evaluator', '\n'*5)
        print('len(self.all_pop_run_X)', len(self.all_pop_run_X))
        print('len(self.problem.objectives_list)', len(self.problem.objectives_list))
        self.all_pop_run_X = pop.get("X")


        # that call is a dummy survival to set attributes that are necessary for the mating selection
        if self.survival:
            pop = self.survival.do(self.problem, pop, len(pop), algorithm=self, n_min_infeas_survive=self.min_infeas_pop_size)

        self.pop, self.off = pop, pop


class MyEvaluator(Evaluator):
    def __init__(self, correct_spawn_locations_after_run=0, correct_spawn_locations=None, **kwargs):
        super().__init__()
        self.correct_spawn_locations_after_run = correct_spawn_locations_after_run
        self.correct_spawn_locations = correct_spawn_locations
    def _eval(self, problem, pop, **kwargs):

        super()._eval(problem, pop, **kwargs)
        if self.correct_spawn_locations_after_run:
            correct_spawn_locations_all(pop[i].X, problem.labels)


def run_nsga2_dt(fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation):

    end_when_no_critical_region = True
    cumulative_info = None

    X_filtered = None
    F_filtered = None
    X = None
    y = None
    F = None
    labels = None
    estimator = None
    critical_unique_leaves = None

    now = datetime.now()
    dt_time_str = now.strftime("%Y_%m_%d_%H_%M_%S")

    if fuzzing_arguments.warm_up_path:
        subfolders = get_sorted_subfolders(fuzzing_arguments.warm_up_path)
        X, _, objectives_list, _, _, _ = load_data(subfolders)

        if fuzzing_arguments.warm_up_len > 0:
            X = X[:fuzzing_arguments.warm_up_len]
            objectives_list = objectives_list[:fuzzing_arguments.warm_up_len]

        y = determine_y_upon_weights(objectives_list, fuzzing_arguments.objective_weights)
        F = get_F(objectives_list, objectives_list, fuzzing_arguments.objective_weights, fuzzing_arguments.use_single_objective)

        estimator, inds, critical_unique_leaves = filter_critical_regions(np.array(X), y)
        X_filtered = np.array(X)[inds]
        F_filtered = F[inds]

    for i in range(fuzzing_arguments.outer_iterations):
        dt_time_str_i = dt_time_str
        dt = True
        if (i == 0 and not fuzzing_arguments.warm_up_path) or np.sum(y)==0:
            dt = False


        dt_arguments = emptyobject(
            call_from_dt=True,
            dt=dt,
            X=X_filtered,
            F=F_filtered,
            estimator=estimator,
            critical_unique_leaves=critical_unique_leaves,
            dt_time_str=dt_time_str_i, dt_iter=i, cumulative_info=cumulative_info)


        X_new, y_new, F_new, _, labels, parent_folder, cumulative_info, all_pop_run_X, objective_list, objective_weights = run_ga(fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation, dt_arguments=dt_arguments)


        if fuzzing_arguments.finish_after_has_run and cumulative_info['has_run'] > fuzzing_arguments.has_run_num:
            break

        if len(X_new) == 0:
            break

        if i == 0 and not fuzzing_arguments.warm_up_path:
            X = X_new
            y = y_new
            F = F_new
        else:
            X = np.concatenate([X, X_new])
            y = np.concatenate([y, y_new])
            F = np.concatenate([F, F_new])

        estimator, inds, critical_unique_leaves = filter_critical_regions(X, y)
        # print(X, F, inds)
        X_filtered = X[inds]
        F_filtered = F[inds]

        if len(X_filtered) == 0 and end_when_no_critical_region:
            break

    # save running results summary in a pickle file
    x_list_all = []
    y_list_all = []
    objective_list_all = []
    for i in range(fuzzing_arguments.outer_iterations):
        with open(os.path.join(fuzzing_arguments.parent_folder, i, 'data.pickle'), 'rb') as f_in:
            data_d = pickle.load(f_in)
            x_list = data_d['x_list']
            y_list = data_d['y_list']
            objective_list = data_d['objective_list']

            x_list_all.append(x_list)
            y_list_all.append(y_list)
            objective_list_all.append(objective_list)
    data_d['x_list'] = np.concatenate(x_list_all)
    data_d['y_list'] = np.concatenate(y_list_all)
    data_d['objective_list'] = np.concatenate(objective_list_all)
    with open(os.path.join(fuzzing_arguments.parent_folder, 'data.pickle'), 'wb') as f_out:
        pickle.dump(data_d, f_out)


def run_ga(fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation, dt_arguments=None):

    if not dt_arguments:
        dt_arguments = emptyobject(
            call_from_dt=False,
            dt=False,
            X=None,
            F=None,
            estimator=None,
            critical_unique_leaves=None,
            dt_time_str=None, dt_iter=None, cumulative_info=None)

    if dt_arguments.call_from_dt:
        fuzzing_arguments.termination_condition = 'generations'
        if dt_arguments.dt and len(list(dt_arguments.X)) == 0:
            print('No critical leaves!!! Start from random sampling!!!')
            dt_arguments.dt = False

        time_str = dt_arguments.dt_time_str

    else:
        now = datetime.now()
        p, c, th = fuzzing_arguments.check_unique_coeff
        time_str = now.strftime("%Y_%m_%d_%H_%M_%S")+','+'_'.join([str(fuzzing_arguments.pop_size), str(fuzzing_arguments.n_gen), fuzzing_arguments.rank_mode, str(fuzzing_arguments.has_run_num), 'coeff', str(p), str(c), str(th), 'only_unique', str(fuzzing_arguments.only_run_unique_cases)])

    if fuzzing_arguments.simulator == 'no_simulation_function':
        cur_parent_folder = make_hierarchical_dir([fuzzing_arguments.root_folder, fuzzing_arguments.algorithm_name, fuzzing_arguments.synthetic_function, time_str])
    elif fuzzing_arguments.simulator == 'no_simulation_dataset':
        cur_parent_folder = make_hierarchical_dir([fuzzing_arguments.root_folder, fuzzing_arguments.algorithm_name, time_str])
    else:
        cur_parent_folder = make_hierarchical_dir([fuzzing_arguments.root_folder, fuzzing_arguments.algorithm_name, fuzzing_arguments.route_type, fuzzing_arguments.scenario_type, fuzzing_arguments.ego_car_model, time_str])

    if dt_arguments.call_from_dt:
        parent_folder = make_hierarchical_dir([cur_parent_folder, str(dt_arguments.dt_iter)])
    else:
        parent_folder = cur_parent_folder

    fuzzing_arguments.parent_folder = parent_folder
    fuzzing_arguments.mean_objectives_across_generations_path = os.path.join(parent_folder, 'mean_objectives_across_generations.txt')

    problem = MyProblem(fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation, dt_arguments)


    # deal with real and int separately
    crossover = MixedVariableCrossover(problem.mask, {
        "real": get_crossover("real_sbx", prob=0.8, eta=5),
        "int": get_crossover("int_sbx", prob=0.8, eta=5)
    })

    # hack: changed from int(prob=0.05*problem.n_var) to prob=0.4
    if fuzzing_arguments.algorithm_name in ['avfuzzer']:
        mutation_prob = 0.4
    else:
        mutation_prob = int(0.05*problem.n_var)
    mutation = MixedVariableMutation(problem.mask, {
        "real": get_mutation("real_pm", eta=5, prob=mutation_prob),
        "int": get_mutation("int_pm", eta=5, prob=mutation_prob)
    })
    selection = TournamentSelection(func_comp=binary_tournament)
    repair = ClipRepair()
    eliminate_duplicates = NoDuplicateElimination()
    mating = MyMatingVectorized(selection,
                    crossover,
                    mutation,
                    fuzzing_arguments.use_unique_bugs,
                    fuzzing_arguments.emcmc,
                    fuzzing_arguments.mating_max_iterations,
                    repair=repair,
                    eliminate_duplicates=eliminate_duplicates)

    # extra mating methods for avfuzzer
    local_mutation = MixedVariableMutation(problem.mask, {
            "real": get_mutation("real_pm", eta=5, prob=0.6),
            "int": get_mutation("int_pm", eta=5, prob=0.6)
        })
    local_mating = MyMatingVectorized(selection,
                    crossover,
                    local_mutation,
                    fuzzing_arguments.use_unique_bugs,
                    fuzzing_arguments.emcmc,
                    fuzzing_arguments.mating_max_iterations,
                    repair=repair,
                    eliminate_duplicates=eliminate_duplicates)

    random_sampling = MySamplingVectorized(random_seed=fuzzing_arguments.random_seed, use_unique_bugs=False, check_unique_coeff=problem.check_unique_coeff, sample_multiplier=fuzzing_arguments.sample_multiplier)



    # For grid search
    if fuzzing_arguments.algorithm_name == 'grid':
        from carla_specific_utils.grid import grid_dict_dict
        assert fuzzing_arguments.grid_dict_name
        grid_start_index = fuzzing_arguments.grid_start_index
        grid_dict = grid_dict_dict[fuzzing_arguments.grid_dict_name]
        sampling = GridSampling(random_seed=fuzzing_arguments.random_seed, grid_start_index=grid_start_index, grid_dict=grid_dict)
    elif fuzzing_arguments.algorithm_name == 'random_local_sphere':
        sampling = RandomDirectionSampling(random_seed=fuzzing_arguments.random_seed, chosen_labels=fuzzing_arguments.chosen_labels)
    else:
        sampling = MySamplingVectorized(random_seed=fuzzing_arguments.random_seed, use_unique_bugs=fuzzing_arguments.use_unique_bugs, check_unique_coeff=problem.check_unique_coeff, sample_multiplier=fuzzing_arguments.sample_multiplier)

    algorithm = NSGA2_CUSTOMIZED(dt=dt_arguments.dt, X=dt_arguments.X, F=dt_arguments.F, fuzzing_arguments=fuzzing_arguments, random_sampling=random_sampling, local_mating=local_mating, sampling=sampling,
    crossover=crossover,
    mutation=mutation,
    eliminate_duplicates=eliminate_duplicates,
    repair=repair,
    mating=mating)


    # close simulator(s)
    atexit.register(exit_handler, fuzzing_arguments.ports)

    if fuzzing_arguments.termination_condition == 'generations':
        termination = ('n_gen', fuzzing_arguments.n_gen)
    elif fuzzing_arguments.termination_condition == 'max_time':
        termination = ('time', fuzzing_arguments.max_running_time)
    else:
        termination = ('n_gen', fuzzing_arguments.n_gen)
    termination = get_termination(*termination)


    if hasattr(sim_specific_arguments, 'correct_spawn_locations_after_run'):
        correct_spawn_locations_after_run = sim_specific_arguments.correct_spawn_locations_after_run
        correct_spawn_locations = sim_specific_arguments.correct_spawn_locations
    else:
        correct_spawn_locations_after_run = False
        correct_spawn_locations = None



    # initialize the algorithm object given a problem
    algorithm.initialize(problem, termination=termination, seed=0,
    verbose=False,
    save_history=False,
    evaluator=MyEvaluator(correct_spawn_locations_after_run=correct_spawn_locations_after_run, correct_spawn_locations=correct_spawn_locations))
    # actually execute the algorithm
    algorithm.solve()

    print('We have found', len(problem.bugs), 'bugs in total.')


    # save running results summary in a pickle file
    # print('np.array(problem.x_list).shape', np.array(problem.x_list).shape)
    # print('np.array(problem.objectives_list).shape', np.array(problem.objectives_list).shape)
    data_d = {
        'x_list': np.array(problem.x_list),
        'objective_list': np.array(problem.objectives_list),
        'y_list': np.array(problem.y_list),
        'labels': np.array(problem.labels),
        'xl': np.array(problem.xl),
        'xu': np.array(problem.xu),
        'mask': np.array(problem.mask),
        'parameters_min_bounds': problem.parameters_min_bounds,
        'parameters_max_bounds': problem.parameters_max_bounds,
    }
    with open(os.path.join(fuzzing_arguments.parent_folder, 'data.pickle'), 'wb') as f_out:
        pickle.dump(data_d, f_out)

    # additional saving for random_local_sphere
    # if fuzzing_arguments.algorithm_name in ['random_local_sphere']:
    #     with open(os.path.join(fuzzing_arguments.parent_folder, 'spheres.pickle'), 'wb') as f_out:
    #         print('len(algorithm.sampling.spheres[0].members', len(algorithm.sampling.spheres[0].members))
    #         print('len(algorithm.sampling.spheres[1].members', len(algorithm.sampling.spheres[1].members))
    #         pickle.dump(algorithm.sampling.spheres, f_out)

    if len(problem.x_list) > 0:
        X = np.stack(problem.x_list)
        F = np.concatenate(problem.F_list)
        objectives = np.stack(problem.objectives_list)
    else:
        X = []
        F = []
        objectives = []

    y = np.array(problem.y_list)
    time_list = np.array(problem.time_list)
    bugs_num_list = np.array(problem.bugs_num_list)
    unique_bugs_num_list = np.array(problem.unique_bugs_num_list)
    labels = problem.labels
    has_run = problem.has_run
    has_run_list = problem.has_run_list

    mask = problem.mask
    xl = problem.xl
    xu = problem.xu
    p = problem.p
    c = problem.c
    th = problem.th


    cumulative_info = {
        'has_run': problem.has_run,
        'start_time': problem.start_time,
        'counter': problem.counter,
        'time_list': problem.time_list,
        'bugs': problem.bugs,
        'unique_bugs': problem.unique_bugs,
        'interested_unique_bugs': problem.interested_unique_bugs,
        'bugs_type_list': problem.bugs_type_list,
        'bugs_inds_list': problem.bugs_inds_list,
        'bugs_num_list': problem.bugs_num_list,
        'unique_bugs_num_list': problem.unique_bugs_num_list,
        'has_run_list': problem.has_run_list
    }

    return X, y, F, objectives, labels, cur_parent_folder, cumulative_info, algorithm.all_pop_run_X, problem.objectives_list, problem.objective_weights



def run_ga_general(fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation):
    if fuzzing_arguments.algorithm_name in ['nsga2-un-dt', 'nsga2-dt']:
        run_nsga2_dt(fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation)
    else:
        run_ga(fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation)

if __name__ == '__main__':
    '''
    fuzzing_arguments: parameters needed for the fuzzing process, see argparse for details.

    sim_specific_arguments: parameters specific to the simulator used.

    fuzzing_content: a description of the search space.
        labels:
        mask:
        parameters_min_bounds:
        parameters_max_bounds:
        parameters_distributions:
        customized_constraints:
        customized_center_transforms:
        n_var:
        fixed_hyperparameters:
        search_space_info:

    run_simulation(x, fuzzing_content, fuzzing_arguments, sim_specific_arguments, ...) -> objectives, run_info: a simulation function specific to the simulator used.
        objectives:
        run_info:
    '''

    set_start_method('spawn')
    if fuzzing_arguments.simulator == 'carla':
        from carla_specific_utils.scene_configs import customized_bounds_and_distributions
        from carla_specific_utils.setup_labels_and_bounds import generate_fuzzing_content
        from carla_specific_utils.carla_specific import run_carla_simulation, initialize_carla_specific, correct_spawn_locations_all, get_unique_bugs, choose_weight_inds, determine_y_upon_weights, get_all_y

        assert len(fuzzing_arguments.objective_weights) == 10
        fuzzing_arguments.objective_labels = ['ego_linear_speed', 'min_d', 'd_angle_norm', 'offroad_d', 'wronglane_d', 'dev_dist', 'is_offroad', 'is_wrong_lane', 'is_run_red_light', 'is_collision']

        customized_config = customized_bounds_and_distributions[fuzzing_arguments.scenario_type]
        fuzzing_content = generate_fuzzing_content(customized_config)
        sim_specific_arguments = initialize_carla_specific(fuzzing_arguments)
        run_simulation = run_carla_simulation


    elif fuzzing_arguments.simulator == 'svl':
        from svl_script.scene_configs import customized_bounds_and_distributions
        from svl_script.setup_labels_and_bounds import generate_fuzzing_content
        from svl_script.svl_specific import run_svl_simulation, initialize_svl_specific, get_unique_bugs, choose_weight_inds, determine_y_upon_weights, get_all_y


        assert fuzzing_arguments.ego_car_model in ['apollo_6_with_signal', 'apollo_6_modular', 'apollo_6_modular_2gt', 'apollo_6']
        assert fuzzing_arguments.route_type in ['BorregasAve_forward', 'BorregasAve_left', 'SanFrancisco_forward']
        assert fuzzing_arguments.scenario_type in ['default', 'turn_left_one_ped_and_one_vehicle', 'one_ped_crossing', 'go_across_junction_sf', 'go_across_junction_ba', 'one_angle_ped_crossing']

        assert len(fuzzing_arguments.objective_weights) == 10
        # The later fields are ignored for now
        fuzzing_arguments.objective_labels = ['ego_linear_speed', 'min_d', 'npc_collisions', 'diversity'] + ['']*6

        fuzzing_arguments.ports = [8181]
        fuzzing_arguments.root_folder = 'svl_script/run_results_svl'

        customized_config = customized_bounds_and_distributions[fuzzing_arguments.scenario_type]
        fuzzing_content = generate_fuzzing_content(customized_config)
        sim_specific_arguments = initialize_svl_specific(fuzzing_arguments)
        run_simulation = run_svl_simulation

    elif fuzzing_arguments.simulator == 'carla_op':
        sys.path.append('../openpilot')
        sys.path.append('../openpilot/tools/sim')

        from tools.sim.op_script.scene_configs import customized_bounds_and_distributions
        from tools.sim.op_script.setup_labels_and_bounds import generate_fuzzing_content
        from tools.sim.op_script.bridge_multiple_sync3 import run_op_simulation
        from tools.sim.op_script.op_specific import initialize_op_specific, get_unique_bugs, choose_weight_inds, determine_y_upon_weights, get_all_y, get_job_results


        fuzzing_arguments.sample_avoid_ego_position = 1

        assert fuzzing_arguments.route_type in ['Town04_Opt_left_highway', 'Town06_Opt_forward', 'Town04_Opt_forward_highway']
        # hack
        fuzzing_arguments.scenario_type = fuzzing_arguments.route_type
        fuzzing_arguments.root_folder = 'run_results_op'


        assert fuzzing_arguments.ego_car_model in ['op', 'op_radar', 'mathwork_in_lane', 'mathwork_all', 'mathwork_moving', 'best_sensor', 'ground_truth']

        assert len(fuzzing_arguments.objective_weights) == 7
        # fuzzing_arguments.objective_weights = np.array([1., 0., 0., 0., -1., -2., -1.])
        fuzzing_arguments.default_objectives = np.array([130., 0., 0., 1., 0., 0., 0.])
        fuzzing_arguments.objective_labels = ['min_d', 'collision', 'speed', 'd_angle_norm', 'is_bug', 'fusion_error_perc', 'diversity']

        customized_config = customized_bounds_and_distributions[fuzzing_arguments.scenario_type]
        fuzzing_content = generate_fuzzing_content(customized_config)
        sim_specific_arguments = initialize_op_specific(fuzzing_arguments)
        run_simulation = run_op_simulation

    elif fuzzing_arguments.simulator == 'no_simulation_dataset':
        from no_simulation_dataset_script.no_simulation_specific import generate_fuzzing_content, run_no_simulation, initialize_no_simulation_specific
        from no_simulation_dataset_script.no_simulation_objectives_and_bugs import get_unique_bugs, choose_weight_inds, determine_y_upon_weights, get_all_y

        assert fuzzing_arguments.no_simulation_data_path, 'no fuzzing_arguments.no_simulation_data_path is specified.'

        fuzzing_arguments.root_folder = 'no_simulation_dataset_script/run_results_no_simulation'


        # These need to be modified to fit one's requirements for objectives
        fuzzing_arguments.objective_weights = np.array([1., 1., 1., -1., 0., 0.])
        fuzzing_arguments.default_objectives = np.array([20., 1, 10, -1, 0, 0])
        fuzzing_arguments.objective_labels = ['min_dist', 'min_angle', 'min_ttc', 'collision_speed', 'collision', 'oob']
        scenario_labels = ['ego_pos', 'ego_init_speed', 'other_pos', 'other_init_speed', 'ped_delay', 'ped_init_speed']





        scenario_label_types = ['real']*len(scenario_labels)

        fuzzing_content = generate_fuzzing_content(fuzzing_arguments, scenario_labels, scenario_label_types)
        sim_specific_arguments = initialize_no_simulation_specific(fuzzing_arguments)
        run_simulation = run_no_simulation

    elif fuzzing_arguments.simulator == 'no_simulation_function':
        from no_simulation_function_script.no_simulation_specific import generate_fuzzing_content, run_no_simulation, initialize_no_simulation_specific
        from no_simulation_function_script.no_simulation_objectives_and_bugs import get_unique_bugs, choose_weight_inds, determine_y_upon_weights, get_all_y

        fuzzing_arguments.root_folder = 'no_simulation_function_script/run_results_no_simulation'

        fuzzing_arguments.no_simulation_data_path = ''

        # These fields need to be set to be consistent with the synthetic_function used
        fuzzing_arguments.objective_weights = np.array([1.])
        fuzzing_arguments.default_objectives = np.array([1.])
        fuzzing_arguments.objective_labels = ['surrogate_value']

        scenario_labels = ['x1', 'x2']
        scenario_label_types = ['real']*len(scenario_labels)
        min_bounds = [-1]*len(scenario_labels)
        max_bounds = [1]*len(scenario_labels)

        # synthetic function needs to be specified
        assert fuzzing_arguments.synthetic_function

        # used only when algorithm_name == 'random_local_sphere'
        # fuzzing_arguments.chosen_labels = ['x1', 'x2']

        fuzzing_content = generate_fuzzing_content(fuzzing_arguments, scenario_labels, scenario_label_types, min_bounds, max_bounds)
        sim_specific_arguments = initialize_no_simulation_specific(fuzzing_arguments)
        run_simulation = run_no_simulation

    else:
        raise
    run_ga_general(fuzzing_arguments, sim_specific_arguments, fuzzing_content, run_simulation)

# -*- coding: utf-8 -*-
"""The main file for the FLAME algorithm"""

# author: Neha Gupta, Duke University
# Copyright Duke University 2020
# License: MIT

import pandas as pd
import numpy as np
from . import grouped_mr
from . import dame_algorithm
from . import flame_dame_helpers
from . import shared_memory
import joblib
from joblib import Parallel, delayed, parallel_backend
import pickle
import time
import os

# Helper function only for n_jobs>1 (multiprocessing)
def possible_drop(poss_drop, all_covs, consider_dropping, prev_drop, df_all,
                treatment_column_name, outcome_column_name, df_holdout_array_shared,
                adaptive_weights, alpha_given, df_unmatched_shared, return_matches,
                C, weight_array, return_all=True ):
    # S is the set of covars we drop. We try dropping each one
    start = time.time()
    
    df_holdout_array = [df_holdout_array_shared[i].read() for i in range(len(df_holdout_array_shared))]
    df_all_temp = df_all.read().copy(deep=True)
    df_unmatched = df_unmatched_shared.read().copy(deep=True)
    # return_matches_save = return_matches.read().copy(deep=True).astype('Int64')
    # return_matches_temp = return_matches_save.replace(-100, '*')
    
    with open(return_matches, 'rb') as handle:
        return_matches_temp = pickle.load(handle)

    
    s = prev_drop.union([poss_drop])

    PE = flame_dame_helpers.find_pe_for_covar_set(
    df_holdout_array, treatment_column_name, outcome_column_name, s,
    adaptive_weights, alpha_given)

    # error check. PE can be float(0), but not denote error
    if not PE and type(PE) == bool:
        return False, False, False, False, False

    # The dropping criteria for FLAME is max MQ
    # MQ = C * BF - PE

    all_covs = set(all_covs)
    covs_match_on = all_covs.difference([poss_drop]).difference(prev_drop)
    covs_match_on = list(covs_match_on)
    
    matched_rows, return_matches_temp, units_in_g = grouped_mr.algo2_GroupedMR(
        df_all_temp, df_unmatched, covs_match_on, all_covs,
        treatment_column_name, outcome_column_name, return_matches_temp)
    

    # find the BF for this covariate set's match.
    BF = flame_dame_helpers.compute_bf(
        matched_rows, treatment_column_name, df_unmatched)

    # Use the largest MQ as the covariate set to drop.
    MQ = C * BF - PE
    
    if return_all:
        result = {'mq': MQ, 'PE': PE, 'BF': BF, 'poss_drop': poss_drop, \
              'return_matches_temp': return_matches_temp, 'matched_rows': matched_rows, 'units_in_g': units_in_g}
    else:
        result = {'mq': MQ, 'poss_drop': poss_drop}
    return result

def get_largest_mq(results):
    start = time.time()
    result =  max(results, key=lambda x:x['mq'])
    print('get_largest_mq:', time.time() - start)
    return result

def decide_drop(all_covs, consider_dropping, prev_drop, df_all,
                treatment_column_name, outcome_column_name, df_holdout_array,
                adaptive_weights, alpha_given, df_unmatched, return_matches,
                C, weight_array, n_cpus):
    """
    This is a helper function, where we decide which covar to drop next
    Args:
        all_covs (array): Array of covar column names, not including treatment
            and outcome columns.
        consider_dropping (set): Covariate column names that have not yet
            been dropped in a previous iteration
        prev_drop (set): Covariate column names that have been dropped
            in a previous iteration
        n_cpus (int): Define the maximum number of CPUs to be used for parallelizing 
            a for loop in the decide_drop function of the FLAME algorithm. The actual 
            number of CPUs used will be min(n_cpus, cpu_count, number_dropping), where 
            cpu_count is the number of CPUs found in your system, and number_dropping
            is the number of covariates to be dropped in each iteration.     """

    # This is where we decide who to drop, and also compute the pe
    # value that gets outputted in the list described in readme.
    best_drop = 0
    best_mq = float("-inf")
    best_return_matches = 0
    best_matched_rows = 0
    best_bf = 0
    best_pe = 0
    best_units_in_g = 0
    
    cpu_count = joblib.cpu_count()
    n_jobs = min(n_cpus, cpu_count, len(consider_dropping))

    '''
    Copy df to shared memory for fast access, only for multiprocessing
    '''
    if n_jobs > 1:
        df_all_shared = shared_memory.SharedPandasDataFrame(df_all)
        df_holdout_array_shared = [shared_memory.SharedPandasDataFrame(df_holdout_array[i]) for i in range(len(df_holdout_array))]
        df_unmatched_shared = shared_memory.SharedPandasDataFrame(df_unmatched)
        # Need to convert to float64 so the dtype of the DataFrame is not 'object' 
        # If the DataFrame is an object and we copy it to shared memory, there will be a segment fault error since an object 
        # DataFrame is passing by reference, and other processes cannot access the correct values
        # return_matches_save = return_matches.copy(deep=True).replace('*', -100).astype('float64')
        
        with open('./return_matches.pkl', 'wb') as handle:
            pickle.dump(return_matches, handle, protocol=pickle.HIGHEST_PROTOCOL)
        return_matches_shared = './return_matches.pkl'

        
    if not adaptive_weights:
        # find the covariate that can be dropped with the minimum value in
        # the weight array
        min_covar_weight = 1
        best_drop = 0
        for poss_drop in consider_dropping:
            index_in_all_covs = all_covs.index(poss_drop)
            covar_weight = weight_array[index_in_all_covs]
            if covar_weight < min_covar_weight:
                min_covar_weight = covar_weight
                best_drop = poss_drop

        all_covs = set(all_covs)
        covs_match_on = all_covs.difference([best_drop]).difference(prev_drop)
        covs_match_on = list(covs_match_on)

        # need to make sure we don't edit the mutable dataframes, then do match
        
        df_all_temp = df_all.copy(deep=True)
        return_matches_temp1 = return_matches.copy(deep=True)
        
        matched_rows, return_matches, units_in_g = grouped_mr.algo2_GroupedMR(
            df_all_temp, df_unmatched, covs_match_on, all_covs,
            treatment_column_name, outcome_column_name, return_matches_temp1)

        # find the BF for this covariate set's match.
        BF = flame_dame_helpers.compute_bf(matched_rows,
                                           treatment_column_name, df_unmatched)
        return best_drop, 0, matched_rows, return_matches, BF, units_in_g
    else:
        #timing the decide_drop function
        results = []
        print(f"Consider dropping: {len(consider_dropping)}. Using {n_jobs} CPUs")
        
        if n_jobs == 1:
            for poss_drop in consider_dropping:
                # S is the set of covars we drop. We try dropping each one
                s = prev_drop.union([poss_drop])

                PE = flame_dame_helpers.find_pe_for_covar_set(
                    df_holdout_array, treatment_column_name, outcome_column_name, s,
                    adaptive_weights, alpha_given)

                # error check. PE can be float(0), but not denote error
                if not PE and type(PE) == bool:
                    return False, False, False, False, False

                # The dropping criteria for FLAME is max MQ
                # MQ = C * BF - PE

                all_covs = set(all_covs)
                covs_match_on = all_covs.difference([poss_drop]).difference(prev_drop)
                covs_match_on = list(covs_match_on)

                # need to make sure we don't edit the mutable dataframes, then do match
                df_all_temp = df_all.copy(deep=True)
                return_matches_temp = return_matches.copy(deep=True)
                matched_rows, return_matches_temp, units_in_g = grouped_mr.algo2_GroupedMR(
                    df_all_temp, df_unmatched, covs_match_on, all_covs,
                    treatment_column_name, outcome_column_name, return_matches_temp)

                # find the BF for this covariate set's match.
                BF = flame_dame_helpers.compute_bf(
                    matched_rows, treatment_column_name, df_unmatched)

                # Use the largest MQ as the covariate set to drop.
                MQ = C * BF - PE
                if MQ > best_mq:
                    best_mq = MQ
                    best_pe = PE
                    best_bf = BF
                    best_drop = poss_drop
                    best_return_matches = return_matches_temp
                    best_matched_rows = matched_rows
                    best_units_in_g = units_in_g
                
        else:
            results_reduced = Parallel(n_jobs=n_jobs, verbose=2)(\
                delayed(possible_drop)(poss_drop, all_covs, consider_dropping, prev_drop, df_all_shared,\
                treatment_column_name, outcome_column_name, df_holdout_array_shared,\
                adaptive_weights, alpha_given, df_unmatched_shared, return_matches_shared,\
                C, weight_array,return_all=False ) for poss_drop in consider_dropping)
            max_mq = get_largest_mq(results_reduced)
            result_max = possible_drop(max_mq['poss_drop'], all_covs, consider_dropping, prev_drop, df_all_shared,\
                treatment_column_name, outcome_column_name, df_holdout_array_shared,\
                adaptive_weights, alpha_given, df_unmatched_shared, return_matches_shared,\
                C, weight_array, return_all=True )

            best_mq = result_max['mq']
            best_pe = result_max['PE']
            best_bf = result_max['BF']
            best_drop = result_max['poss_drop']
            best_return_matches = result_max['return_matches_temp']
            best_matched_rows = result_max['matched_rows']
            best_units_in_g = result_max['units_in_g']
        
            df_all_shared.unlink()
            [obj.unlink() for obj in df_holdout_array_shared]
            df_unmatched_shared.unlink()
            # return_matches_shared.unlink()
            os.remove(return_matches_shared)

        return best_drop, best_pe, best_matched_rows, best_return_matches, best_bf, best_units_in_g

def flame_generic(df_all, treatment_column_name, weight_array,
                  outcome_column_name, adaptive_weights, alpha, df_holdout,
                  repeats, want_pe, verbose, want_bf, missing_holdout_replace,
                  early_stops, pre_dame, C, n_cpus):
    '''
    All variables are the same as dame algorithm 1 except for:
    pre_dame(False, integer): Indicates whether the algorithm will move to
    DAME and after integer number of iterations.
    '''

    # Initialize variables. These are all moving/temporary throughout algo
    all_covs = df_all.columns.tolist()
    all_covs.remove(treatment_column_name)
    all_covs.remove(outcome_column_name)
    df_unmatched = df_all.copy(deep=True)

    # The items getting returned
    return_pe = [] # list of predictive errors,
    return_bf = []
    MG_units = [] # list of unit ids for each matched group
    # weights indicates the number of times each unit appears in a group
    weights = pd.DataFrame(np.zeros(shape=(len(df_all.index), 1)),
                           columns=['weights'],
                           index=df_all.index)

    return_matches = pd.DataFrame(columns=all_covs, index=df_all.index)

    # Initialize variables used in checking stopping criteria
    orig_len_df_all = len(df_all) # Need this bc of case where repeats=False
    orig_tot_treated = df_all[treatment_column_name].sum()

    h = 0 # Iteration (0'th round of matching is exact matching)
						
    covs_match_on = all_covs
    matched_rows, return_matches, units_in_g = grouped_mr.algo2_GroupedMR(
        df_all, df_unmatched, covs_match_on, all_covs,
        treatment_column_name, outcome_column_name, return_matches)
        
    if (len(units_in_g)) != 0:
        bf = flame_dame_helpers.compute_bf(matched_rows, treatment_column_name, df_unmatched)

				# add the newly matched groups to MG_units, which tracks units in groups
        MG_units = MG_units + units_in_g
        # update unit weights for all units which appear in the new groups
        # flatten to 1 list, then add occurrences of unique units
        flat_units_in_g = np.concatenate(units_in_g).ravel()
        unique_units, occurrences = np.unique(flat_units_in_g, return_counts=True)
        weights.loc[unique_units,'weights'] += occurrences
    else:
        bf = 0

		# Balancing factor of any exact matches
    return_bf.append(bf)

    # Now remove the matched units
    df_unmatched.drop(matched_rows.index, inplace=True)

    if not repeats:
        df_all = df_unmatched

    # set up all the extra dfs if needed
    if missing_holdout_replace:
        # now df_holdout is actually an array of imputed datasets
        df_holdout_array = flame_dame_helpers.create_mice_dfs(
            df_holdout, missing_holdout_replace, outcome_column_name)
    else:
        # df_holdout_array exists regardless, just size 1 and equal to itself
        # if not doing mice.
        df_holdout_array = list()
        df_holdout_array.append(df_holdout)

		# Predictive error of starting covariate set
    baseline_pe = flame_dame_helpers.find_pe_for_covar_set(
				df_holdout_array, treatment_column_name, outcome_column_name, [],
				adaptive_weights, alpha)
    return_pe.append(baseline_pe)

    if verbose == 3:
        flame_dame_helpers.verbose_output(h, len(MG_units),
            df_unmatched[treatment_column_name].sum(), len(df_unmatched),
            orig_len_df_all, orig_tot_treated, baseline_pe, orig_len_df_all, set())

    prev_iter_num_unmatched = len(df_unmatched) # this is for output progress
    consider_dropping = set(i for i in all_covs)
    prev_dropped = set()
    
    # Here, we begin the iterative dropping procedure of FLAME
    while True:
        # see if any stopping criteria have been met        
        if (flame_dame_helpers.stop_iterating(early_stops, df_unmatched,
                                              repeats, treatment_column_name,
                                              orig_len_df_all, h,
                                              orig_tot_treated,
                                              consider_dropping)):
            break
        # one additional stopping criteria check:
        if (len(consider_dropping) == 1):
            print((orig_len_df_all - len(df_unmatched)), "units matched. "\
                  "No more covariate sets to consider dropping")
            break

        h += 1

        new_drop, pe, matched_rows, return_matches, bf, units_in_g = decide_drop(all_covs,
            consider_dropping, prev_dropped, df_all, treatment_column_name,
            outcome_column_name, df_holdout_array, adaptive_weights, alpha,
            df_unmatched, return_matches, C, weight_array, n_cpus)

        # Check for error in above step:
        if not new_drop:
            raise Exception("There may have been an error in your choice of "\
                            "machine learning algorithm used to choose the "\
                            "covariate to drop. For help, please reach on "\
                            "github to the team. ")
        if (len(units_in_g)) != 0:
        # add the newly matched groups to MG_units, which tracks units in groups
            MG_units = MG_units + units_in_g
            # update unit weights for all units which appear in the new groups
            # flatten to 1 list, then add occurrences of unique units
            flat_units_in_g = np.concatenate(units_in_g).ravel()
            unique_units, occurrences = np.unique(flat_units_in_g, return_counts=True)
            weights.loc[unique_units,'weights'] += occurrences

        # Check not equal to false because if it's turned off, value is False
        baseline_pe = max(1e-12, baseline_pe)
        if early_stops.pe and (pe - baseline_pe) / baseline_pe >= early_stops.pe:
            print("Matching stopped while attempting iteration " + str(h) +
            " due to the PE fraction early stopping criterion.")
            print("\tPredictive error of covariate set would have been " + str(pe))
            break
        return_pe.append(pe)

        return_bf.append(bf)

        # Update covariate groups for future iterations
        consider_dropping = consider_dropping.difference([new_drop])
        prev_dropped.add(new_drop)

        # Remove matches
        df_unmatched = df_unmatched.drop(matched_rows.index, errors='ignore')

        if not repeats:
            df_all = df_unmatched

        # End of iter. Prints output based on verbose.
        if verbose == 1:
            print("Completed iteration " + str(h) + " of matching")
        if ((verbose == 2 and (h % 10 == 0)) or verbose == 3):

            flame_dame_helpers.verbose_output(h, len(MG_units),
                df_unmatched[treatment_column_name].sum(), len(df_unmatched),
                orig_len_df_all, orig_tot_treated, pe, prev_iter_num_unmatched,
                new_drop)

            if want_bf:
                print("\tBalancing Factor of this iteration: ", bf)
        # Do we switch to DAME?
        if (pre_dame and pre_dame <= h):

            # drop the columns that have already been matched on
            for i in prev_dropped:
                df_all = df_all.loc[:, df_all.columns.drop(i)]
                df_holdout = df_holdout.loc[:, df_holdout.columns.drop(i)]


            # call dame algorithm
            print((orig_len_df_all - len(df_unmatched)), "units matched. "\
                  "Moving to DAME algorithm")
            return_matches_dame = dame_algorithm.algo1(
                df_all, treatment_column_name, weight_array,
                outcome_column_name, adaptive_weights, alpha, df_holdout,
                repeats, want_pe, verbose, want_bf, missing_holdout_replace,
                early_stops)

            # when dame is done, we
            # return the matches we made here, plus the matches made in dame.
            

            # but first, make sure anything not matched isn't in the df:
            return_matches = return_matches.dropna(axis=0) #drop rows with nan
            return_matches = return_matches.join(weights)
            return_package = [return_matches, MG_units]
            if want_pe:
                return_package.append(return_pe)
            if want_bf:
                return_package.append(return_bf)
            return_package.append(return_matches_dame)
            return return_package

        # end loop.

    return_matches = return_matches.dropna(axis=0) #drop rows with nan
    return_package = [return_matches]

    # append weights and MGs to return package
    return_package[0] = return_package[0].join(weights)
    return_package.append(MG_units)

    if want_pe:
        return_package.append(return_pe)
    if want_bf:
        return_package.append(return_bf)

    return return_package
from typing import Any, Callable, Dict, List, Optional, Union

import dspy
from dspy import logger
from dspy.evaluate.evaluate import Evaluate
from dspy.primitives.example import Example
from dspy.primitives.program import Program
from dspy.primitives.prediction import Prediction
from dspy.signatures.signature import signature_to_template

#-------------------------------------------------------------------------------
#    Templates for the user-facing strings used by this module
#-------------------------------------------------------------------------------

_INFO_DEFAULT_TEACHER = """No teacher provided. Using a copy of the student \
program as the teacher."""

_INFO_STRUCTURAL_EQUIVALENCY = """Ensuring that the student and teacher are \
are structurally equivalent."""

_INFO_SHARED_PREDICTOR = """Ensuring that the student and teacher programs do \
not share predictors."""

_INFO_LM_CONSISTENCY = """Ensuring that the teacher program satisfies the LM \
consistency property."""

_INFO_BOOTSTRAP_DATA = """Bootstrapping data on {} examples with the program \
{}, with {} threads"""

#-------------------------------------------------------------------------------
#    Helper functions
#-------------------------------------------------------------------------------

# TODO: Shared below are useful functions. Similar procedures are implemented
# separately and used by other DSPy teleprompters. These can be moved to shared
# locations.
def prepare_teacher(
        student: Program,
        teacher: Program = None
    ) -> Union[Program, AssertionError]:
    """Prepare the teacher program with respect to the student program.
    
    Args:
        student: The student program.
        teacher: The teacher program. If `None`, a copy of the student program
            is used as the teacher. Defaults to `None`.

    Returns:
        The copied teacher program.
    
    Raises:
        AssertionError: If the teacher is not an instance of the Program class.
    """
    # If teacher is None, use a copy of the student program as the teacher
    if teacher is None:
        logger.info(_INFO_DEFAULT_TEACHER)
        teacher = student.deepcopy()
    else:
        teacher = teacher.deepcopy()

    # Ensure that the student and teacher programs have the same structure
    logger.info(_INFO_STRUCTURAL_EQUIVALENCY)
    student._assert_structural_equivalency(teacher)

    # Ensure that the predictors of the programs point to different objects
    logger.info(_INFO_SHARED_PREDICTOR)
    student._assert_no_shared_predictor(teacher)

    # Ensure that the LM consistency property is satisfied
    logger.info(_INFO_LM_CONSISTENCY)
    teacher._assert_lm_consistency()

    # If the global LM is being used, set it to the LMs of the copied teacher
    # program predictors to to avoid handling the same edge cases later
    if dspy.settings.lm:
        teacher._set_all_predictor_lms(dspy.settings.lm)

    return teacher

# TODO: fix docstring
def convert_to_module_level_message_data(
        data: List[Dict],
        keep_data_keys: bool = False,
        exclude_demos: bool = False,
        try_to_record_lm_kwargs: bool = False,
        program: Program = None
    ) -> List[Dict]:
    """Convert the data to prompt-completion data using the "trace" field.

    This function is a wrapper around the function
    `build_prompt_completion_data_from_trace`, calling it on the "trace" field
    of each dictionary in the input data list and combiningin the results into
    a list of prompt-completion data dictionaries. If the `keep_data_keys`
    is set, the original data keys are also copied over to in the
    prompt-completion dictionaries.

    For example, if the input data includes 10 dictionaries, each containing a
    "trace" field generated by a program with 3 predictors, the returned data
    will have 30 prompt-completion data dictionaries.

    Args:
        data: List of data dictionaries to be converted to prompt-completion
            data. Each dictionary in the list should contain a "trace" field,
            which is passed to the `build_prompt_completion_data_from_trace`
            function to generate the prompt-completion data.
        keep_data_keys: Whether to keep the original data keys in the
            prompt-completion data. Note that if there are keys that are common
            between the original data and the prompt-completion data returned by
            `build_prompt_completion_data_from_trace`, the values from the
            prompt-completion data will overwrite the values from the original
            data. Defaults to `False`.
        exclude_demos: Passed to `build_prompt_completion_data_from_trace`.
            Defaults to `False`.
        try_to_record_lm_kwargs: Passed to
            `build_prompt_completion_data_from_trace`. Defaults to `False`.
        program: Passed to `build_prompt_completion_data_from_trace`.
            Defaults to `None`.
    """
    prompt_completion_data = []
    for data_dict in data:
        trace = data_dict["trace"]
        trace_prompt_comletion_data = build_messages_from_trace(
            trace=trace, exclude_demos=exclude_demos,
            try_to_record_lm_kwargs=try_to_record_lm_kwargs, program=program
        )
        for prompt_completion_dict in trace_prompt_comletion_data:
            if keep_data_keys:
                prompt_completion_dict = {**data_dict, **prompt_completion_dict}
            prompt_completion_data.append(prompt_completion_dict)
    return prompt_completion_data

# TODO: fix docstring
def build_messages_from_trace(
        trace: List[Dict],
        exclude_demos: bool=False,
        try_to_record_lm_kwargs: bool = False,
        program: Program = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
    """Build messages from a given trace.
    """
    messages = []
    # If the program is provided, build the predictor index to name mapping
    if program:
        pred_ind_to_name = {
            ind: name for ind, (name, _) in enumerate(program.named_predictors())
        }

    # Build the prompt-completion data

    adapter = dspy.settings.adapter or dspy.ChatAdapter()
    data = []

    # TODO: Make sure that this works for multi-stage pipelines
    for pred_ind, (pred, inputs, outputs) in enumerate(trace):
        # Get the demos from the predictor if exclude_demos is False
        demos = [] if exclude_demos else pred.demos

        messages = adapter.format(pred.signature, demos, inputs)

        formatted_completion = adapter.format_completion(pred.signature, outputs)
        messages.append({"role": "assistant", "content": formatted_completion})
        data.append(messages)

    return data



def bootstrap_data(
        program: Program,
        dataset: List[Example],
        metric: Optional[Callable[
            [Example, Prediction, Optional[List]], Union[bool, int, float]
        ]] = None,
        num_threads = 1,
        max_errors: int = 0
    ) -> List[Dict[str, Any]]:
    """Bootstrap prediction and trace data for the program using the dataset.
    
    Args:
        program: The program that will be used to generate the traces for data
            collection.
        dataset: The dataset to be used for data collection.
        metric: The optional metric to be used to get a score for the example,
            recorded in a `score` field in the data. If the metric is not
            provided, the `score` field is not included in the data. Defaults
            to `None`.
        num_threads: The number of threads to be used for data collection.
            Defaults to `1`.
    
    Returns:
        Data as a list of dictionaries with the keys `example`, `prediction`,
        `trace`, and optionally, `score` fields. For a given example:
        - The `example` field corresponds to the example itself.
        - The `example_ind` field corresponds to the index of the example in the
            `dataset`.
        - The `prediction` field corresponds to the prediction made by the
            program on the example.
        - The `trace` field corresponds to the trace generated by the program
            on the example.
        - The `score` field corresponds to the metric score of the example, if
            the metric is provided. Otherwise, it is not included in the data.
    """
    data = []

    # Use Evaluate to call the program have the responses cached
    cname = program.__class__.__name__
    info = _INFO_BOOTSTRAP_DATA.format(len(dataset), cname, num_threads)
    logger.info(info)
    evaluator = Evaluate(
        devset=dataset, num_threads=num_threads, display_progress=True, max_errors=max_errors, provide_traceback=True
    )
    x = evaluator(program, metric=metric)
    # print(x)
    data = process_dataset_threaded(dataset, program, metric, num_threads, max_errors)
    
    return data

def process_dataset_threaded(dataset: List[Any], program: Callable, metric: Optional[Callable] = None, max_workers: int = None, max_errors: int = 0) -> List[Dict[str, Any]]:
    data = []
    num_threads = max_workers if max_workers else 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        future_to_example = {executor.submit(process_example, example, i, program, metric): i 
                             for i, example in enumerate(dataset)}
        
        for future in concurrent.futures.as_completed(future_to_example):
            data_dict = future.result()
            if data_dict is not None:
                data.append(data_dict)
    
    # Sort the results based on example_ind to maintain original order
    data.sort(key=lambda x: x['example_ind'])
    
    return data

def process_example(example: Any, example_ind: int, program: Callable, metric: Optional[Callable] = None) -> Dict[str, Any]:
    # print("Processing example:", example_ind)
    with dspy.context(trace=[]):
        # print("Running program...", example_ind)
        try:
            prediction = program(**example.inputs())
        except Exception as e:
            print(f"Error processing example {example_ind}: {e}")
            return None
        # print("Getting trace...", example_ind)
        trace = dspy.settings.trace
        # print("Getting score...", example_ind)
        score = metric(example, prediction, trace) if metric else None

    data_dict = {
        'example': example,
        'prediction': prediction,
        'trace': trace,
        'example_ind': example_ind
    }
    if metric:
        data_dict['score'] = score
    
    return data_dict

# TODO: If we can ensure to pass the "round" information every time a call is
# issued to an LM, we can make repetitive un-cached calls to the same LM without
# modifying it's temperature. This function can be removed then.
def bootstrap_data_for_round(
        program: Program,
        dataset: List[Example],
        metric: Optional[Callable[
            [Example, Prediction, Optional[List]], Union[bool, int, float]
        ]] = None,
        num_threads = 1,
        sampling_round: int = 0,
        sampling_temperature: Optional[float] = 0.9,
        sampling_temperature_delta: float = 0.001,
        max_errors: int = 0
    ) -> Union[List[Dict], AssertionError]:
    """ Bootstrap data for the given sampling round.

    This is a wrapper function around the `bootstrap_data` function that allows
    for collecting data for the given `sampling_round`. Due to the way caching
    works, one cannot get different completions for the same prompt just by
    querying an LM again, despite using a high sampling temperature. This
    function is a workaround to get different completions for the same prompt
    by modifying the sampling temperature of the LM for the specified
    `sampling_round`. The temperature of an LM is set to the following value
    for the given `sampling_round`:
    
        sampling_temperature + sampling_temperature_delta * sampling_round

    If a `sampling_temperature` of `None` is passed, the already set temperature
    of the LM is used as the sampling temperature instead.

    To sample different completions for the same prompt, this sampling can be
    called multiple times with different `sampling_round` values. For example:
    ```
    num_rounds = 5
    data = []
    for round in range(num_rounds):
        data += bootstrap_data_for_round(
            program, dataset, metric=metric, sampling_round=round
        )
        # Any dataset filtering for the next round can be done here, if needed
    ```

    The dataset filtering can become a powerful tool. For example, it can be
    used to filter out the examples that have already had enough high scoring
    completions generated for them. Here is an illustration:
    ```
    num_correct_target = 3
    correct_score = 1
    num_rounds = 5
    data = []
    for round in range(num_rounds):
        data += bootstrap_data_for_round(
            program, dataset, metric=metric, sampling_round=round
        )
        correct_bootstraps = [d for d in data if d['score'] == correct_score]
        correct_counts = Counter([d['example'] for d in correct_bootstraps])
        dataset = [d for d in dataset if correct_counts[d] < num_correct_target]
    ```

    Args:
        program: The program that will be used to generate the traces for data
            collection.
        dataset: The dataset to be used for data collection.
        metric: The optional metric to be used to get a score for the example,
            recorded in a `score` field in the data. If the metric is not
            provided, the `score` field is not included in the data. Defaults to
            `None`.
        num_threads: The number of threads to be used for data collection.
            Defaults to `1`.
        sampling_round: The round index for which the data is being collected.
            Defaults to `0`.
        sampling_temperature: The sampling temperature to be used for round
            `0`. If a value of `None` is passed, the temperature of the LM is
            used as the sampling temperature instead. Defaults to a high
            temperature of `0.9`.
        sampling_temperature_delta: The small temperature delta that's added to
            the sampling temperature every increment of the round index to
            generate different completions for the same prompt. Defaults to
            `0.001`.

    Returns:
        Data as a list of dictionaries with the keys returned by
        the `bootstrap_data` function, descriptions for which are shared in the
        function documentation. This function adds the following extra field
        to the dictionaries:
        - The `round` field corresponds to the `sampling_round` argument.
    """ 
    # Helper function to adjust the temperature of the LM. If a None temperature
    # is passed, keep the LM's temperature as is as the base temperature, then
    # adjust the temperature for the given round.
    def copy_model_with_updated_temp(lm):
        temp = sampling_temperature
        temp = lm.kwargs["temperature"] if temp is None else temp
        temp = temp + sampling_temperature_delta * sampling_round
        return lm.copy(temperature=temp)

    # Ensure that the LM consistency is satisfied, which ensures that either (1)
    # the global LM is set or (2) all the predictors have an LM set.
    # TODO(isaac): Uncomment this line after the LM consistency property is
    # program._assert_lm_consistency()

    # Deepcopy the program and copy the dataset to avoid modifying the original
    program = program.deepcopy()
    dataset = dataset.copy()

    # Update the temperature of the LM for the given round
    context_lm = None
    if dspy.settings.lm:
        context_lm = copy_model_with_updated_temp(dspy.settings.lm)
    else:
        for pred in program.predictors():
            pred.lm = copy_model_with_updated_temp(pred.lm)

    # Collect the data for the given round
    with dspy.context(lm=context_lm):
        # print(context_lm.kwargs)
        data = bootstrap_data(
            program, dataset, metric=metric, num_threads=num_threads, max_errors=max_errors
        )
    
    # Add the round information to the data
    for data_dict in data:
        data_dict["round"] = sampling_round

    return data

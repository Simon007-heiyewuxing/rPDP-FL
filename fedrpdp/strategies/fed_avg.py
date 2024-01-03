import os
import time
import numpy as np
import copy
from typing import List, Union

import torch
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm, trange

from .. import PrivacyEngine
from .utils import DataLoaderWithMemory, _Model
from ..utils import evaluate_model_on_tests

class FedAvg:
    """Federated Averaging Strategy class.

    The Federated Averaging strategy is the most simple centralized FL strategy.
    Each client first trains his version of a global model locally on its data,
    the states of the model of each client are then weighted-averaged and returned
    to each client for further training.

    References
    ----------
    - https://arxiv.org/abs/1602.05629

    Parameters
    ----------
    training_dataloaders : List
        The list of training dataloaders from multiple training centers.
    model : torch.nn.Module
        An initialized torch model.
    loss : torch.nn.modules.loss._Loss
        The loss to minimize between the predictions of the model and the
        ground truth.
    optimizer_class : torch.optim.Optimizer
        The class of the torch model optimizer to use at each step.
    learning_rate : float
        The learning rate to be given to the optimizer_class.
    num_updates : int
        The number of updates to do on each client at each round.
    nrounds : int
        The number of communication rounds to do.
    dp_target_epsilon: float
        The target epsilon for (epsilon, delta)-differential
        private guarantee. Defaults to None.
    dp_target_delta: float
        The target delta for (epsilon, delta)-differential private
        guarantee. Defaults to None.
    dp_max_grad_norm: float
        The maximum L2 norm of per-sample gradients; used to enforce
        differential privacy. Defaults to None.
    log: bool, optional
        Whether or not to store logs in tensorboard. Defaults to False.
    log_period: int, optional
        If log is True then log the loss every log_period batch updates.
        Defauts to 100.
    bits_counting_function : Union[callable, None], optional
        A function making sure exchanges respect the rules, this function
        can be obtained by decorating check_exchange_compliance in
        flamby.utils. Should have the signature List[Tensor] -> int.
        Defaults to None.
    logdir: str, optional
        Where logs are stored. Defaults to ./runs.
    log_basename: str, optional
        The basename of the created log_file. Defaults to fed_avg.
    """

    def __init__(
        self,
        training_dataloaders: List, 
        test_dataloaders: List, # added by Junxu
        model: torch.nn.Module,
        loss: torch.nn.modules.loss._Loss,
        optimizer_class: torch.optim.Optimizer,
        learning_rate: float,
        num_updates: int,
        nrounds: int,
        client_sample_rate: float,
        dp_target_epsilon: Union[float, List[float], List[List[float]]] = None,
        dp_target_delta: float = None,
        dp_max_grad_norm: float = None,
        dp_noise_multiplier: float = None,
        privacy_engine: PrivacyEngine = None,
        device: str = "cuda:0",
        log: bool = False,
        log_period: int = 100,
        bits_counting_function: callable = None,
        logdir: str = "./runs",
        log_basename: str = "fed_avg",
        seed=None,
    ):
        """
        Cf class docstring
        """
        self._seed = seed if seed is not None else int(time.time())

        self.dp_target_epsilon = dp_target_epsilon
        self.dp_target_delta = dp_target_delta
        self.dp_max_grad_norm = dp_max_grad_norm
        self.dp_noise_multiplier = dp_noise_multiplier

        self.log = log
        self.log_period = log_period
        self.log_basename = log_basename
        self.logdir = logdir
        if self.log:
            os.makedirs(logdir, exist_ok=True)
            self.writer = SummaryWriter(
                log_dir=os.path.join(logdir)
            )

        self.nrounds = nrounds
        self.num_updates = num_updates

        self.models_list = [
            _Model(
                model=model,
                optimizer_class=optimizer_class,
                lr=learning_rate,
                train_dl=_train_dl,
                test_dl=_test_dl, # added by Junxu
                dp_target_epsilon=self.dp_target_epsilon[i],
                dp_target_delta=self.dp_target_delta,
                dp_max_grad_norm=self.dp_max_grad_norm,
                dp_noise_multiplier=self.dp_noise_multiplier,
                privacy_engine=copy.deepcopy(privacy_engine),
                device=device,
                loss=loss,
                nrounds=nrounds,
                log=self.log,
                client_id=i,
                log_period=self.log_period,
                log_basename=self.log_basename,
                logdir=self.logdir,
                seed=self._seed,
            )
            for i, (_train_dl, _test_dl) in enumerate(list(zip(training_dataloaders, test_dataloaders)))
        ]
        
        self.training_dataloaders_with_memory = [ DataLoaderWithMemory(m._train_dl) for m in self.models_list]
        self.test_dataloaders = test_dataloaders # added by Junxu. 
        # Why we do not handle it like  training_dataloaders? Cuz train_dl will get changed when we apply dp.

        self.training_sizes = [len(e) for e in self.training_dataloaders_with_memory]
        self.total_number_of_samples = sum(self.training_sizes)

        self.num_clients = len(self.training_sizes)
        self.bits_counting_function = bits_counting_function

    def _local_optimization(self, _model: _Model, dataloader_with_memory, current_round):
        """Carry out the local optimization step.

        Parameters
        ----------
        _model: _Model
            The model on the local device used by the optimization step.
        dataloader_with_memory : dataloaderwithmemory
            A dataloader that can be called infinitely using its get_samples()
            method.
        """
        _model._local_train(dataloader_with_memory, self.num_updates)

    def perform_round(self, current_round:int ):
        """Does a single federated averaging round. The following steps will be
        performed:

        - each model will be trained locally for num_updates batches.
        - the parameter updates will be collected and averaged. Averages will be
          weighted by the number of samples in each client
        - the averaged updates willl be used to update the local model
        """
        local_updates = list()
        for _model, dataloader_with_memory, size in zip(
            self.models_list, self.training_dataloaders_with_memory, self.training_sizes
        ):

            # Local Optimization
            _local_previous_state = _model._get_current_params()
            self._local_optimization(_model, dataloader_with_memory, current_round)
            _local_next_state = _model._get_current_params()

            # Recovering updates
            updates = [
                new - old for new, old in zip(_local_next_state, _local_previous_state)
            ]
            del _local_next_state

            # Reset local model
            for p_new, p_old in zip(_model.model.parameters(), _local_previous_state):
                p_new.data = torch.from_numpy(p_old).to(p_new.device)
            del _local_previous_state

            if self.bits_counting_function is not None:
                self.bits_counting_function(updates)

            local_updates.append({"updates": updates, "n_samples": size})

        # Aggregation step
        aggregated_delta_weights = [
            None for _ in range(len(local_updates[0]["updates"]))
        ]
        for idx_weight in range(len(local_updates[0]["updates"])):
            aggregated_delta_weights[idx_weight] = sum(
                [
                    local_updates[idx_client]["updates"][idx_weight]
                    * local_updates[idx_client]["n_samples"]
                    for idx_client in range(self.num_clients)
                ]
            )
            aggregated_delta_weights[idx_weight] /= float(self.total_number_of_samples)

        # Update models
        for _model in self.models_list:
            _model._update_params(aggregated_delta_weights)

    def run(self, metric, device):
        """This method performs self.nrounds rounds of averaging
        and returns the list of models.
        """
        all_round_results = []
        for r in range(self.nrounds):
            self.perform_round(current_round=r)

            perf, y_true_dict, y_pred_dict = evaluate_model_on_tests(self.models_list[0].model, self.test_dataloaders, device=device, metric=metric, return_pred=True)

            # mean_perf = np.array(
            #     [v for _, v in perf.items()]
            # ).mean()

            correct = np.array(
                [v for _, v in perf.items()]
            ).sum()
            total = np.array(
                [len(v) for _, v in y_true_dict.items()]
            ).sum()
            print(f"Round={r}, perf={list(perf.values())}, mean perf={correct}/{total} ({correct/total:.4f}%)")
            all_round_results.append(round(correct/total, 4))

        return [m.model for m in self.models_list], all_round_results

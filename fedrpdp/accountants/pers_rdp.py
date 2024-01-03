# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import List, Optional, Tuple, Union
import numpy as np
import math

from .accountant import IAccountant
from .rdp import compute_rdp_4fed, compute_rdp_4sgd
from .analysis import rdp as privacy_analysis 

class PersRDPAccountant(IAccountant):
    # DEFAULT_ALPHAS = [1 + x / 10.0 for x in range(1, 100)] + list(range(11, 64))
    
    def __init__(self, 
                 noise_multiplier: float,
                 sample_rate: List[float],
                 inner_steps: int = None,
                 client_rate: float = None):
        super().__init__()

        self.noise_multiplier = noise_multiplier
        self.sample_rate = sample_rate
        self.num_steps = 0
        self.inner_steps = inner_steps
        self.outer_rate = client_rate

        
    def step(self):
        self.num_steps += 1 

        # TODO: restore this function, use self.history 
        # to save the related parameters

    @classmethod
    def generate_rdp_orders(cls):
        dense = 1.07
        alpha_list = [int(dense ** i + 1) for i in range(int(math.floor(math.log(1000, dense))) + 1)]
        alpha_list = np.unique(alpha_list)
        return alpha_list

    def get_privacy_spent(
        self, *, 
        delta: float, 
        sample_rate: float,
        alphas: Optional[List[Union[float, int]]] = None
    ) -> Tuple[float, float]:
        
        assert self.noise_multiplier is not None, "noise_multiplier should not be None"
        
        if alphas is None:
            alphas = self.generate_rdp_orders()

        if self.outer_rate and self.inner_steps:
            rdp = compute_rdp_4fed(alphas=alphas,
                noise_multiplier=self.noise_multiplier, 
                inner_rate=sample_rate, 
                inner_steps=self.inner_steps, 
                outer_rate=self.outer_rate,
                outer_steps=self.num_steps)

        else:
            rdp = compute_rdp_4sgd(alphas=alphas, 
                noise_multiplier=self.noise_multiplier,
                sample_rate=sample_rate, 
                steps=self.num_steps)

        # access the best alpha
        eps, best_alpha = privacy_analysis.get_privacy_spent(
            orders=alphas, rdp=rdp, delta=delta
        )
        return float(eps), float(best_alpha)

    def get_epsilon(
        self, sample_rate: float, delta: float,
        alphas: Optional[List[Union[float, int]]] = None
    ):
        """
        Return privacy budget (epsilon) expended so far.

        Args:
            delta: target delta
            alphas: List of RDP orders (alphas) used to search for the optimal conversion
                between RDP and (epd, delta)-DP
        """
        eps, _ = self.get_privacy_spent(
            sample_rate=sample_rate,
            delta=delta, alphas=alphas)
        return eps

    def __len__(self):
        return len(self.history)

    @classmethod
    def mechanism(cls) -> str:
        return "pers_rdp"


# def _compute_rdp_4sgd(
#     noise_multiplier: float, 
#     alpha: float, 
#     sample_rate: float, 
#     steps: int) -> float:
#     r"""
#     noise_multiplier: The ratio of the standard deviation of the
#             additive Gaussian noise to the L2-sensitivity of the function
#             to which it is added.
#     """
#     assert sample_rate >= 0.0 and sample_rate <= 1.0, "The input `samp_rate` must be a positive real number in [0,1]."
#     assert steps >= 1, "The input `steps` must be a positive integer larger than 1."

#     return privacy_analysis.compute_rdp(
#         q=sample_rate,
#         noise_multiplier=noise_multiplier,
#         steps=steps,
#         orders=alpha
#     )
    
# def _compute_rdp_4fed(
#     noise_multiplier: float, 
#     alpha: float, 
#     inner_rate: float, 
#     outer_rate: float, 
#     inner_steps: int,
#     outer_rounds: int) -> float:
#     r"""
#     noise_multiplier: The ratio of the standard deviation of the
#             additive Gaussian noise to the L2-sensitivity of the function
#             to which it is added.
#     """
#     assert inner_rate >= 0.0 and inner_rate <= 1.0 and outer_rate >= 0.0 and outer_rate <= 1.0, "both the input `inner_rate` and `outer_rate` must be a positive real number in [0,1]."
#     assert inner_steps >= 1, "The input `inner_steps` must be a positive integer larger than 1."

#     inner_rdp = _compute_rdp_4sgd(
#         noise_multiplier=noise_multiplier, 
#         alpha=alpha, 
#         sample_rate=inner_rate, 
#         steps=inner_steps)
    
#     if outer_rate == 1.0: # no outer-level privacy amplification
#         return inner_rdp * outer_rounds
#     else:
#         log_term_1 = np.log(1. - outer_rate)
#         log_term_2 = np.log(outer_rate) + (alpha-1) * inner_rdp
#         outer_rdp = privacy_analysis._log_add(log_term_1, log_term_2)/(alpha-1)
#         return outer_rdp * outer_rounds

# def compute_rdp_fed(
#     *,
#     noise_multiplier: float, 
#     alphas: Union[List[float], float], 
#     inner_rate: float, 
#     outer_rate: float, 
#     inner_steps: int, 
#     outer_rounds: int = None
# ) -> Union[List[float], float]:
    
#     if outer_rounds is None: # sgd setting
#         if isinstance(alphas, float):
#             rdp = _compute_rdp_4sgd(
#                 noise_multiplier=noise_multiplier, 
#                 alpha=alphas, 
#                 sample_rate=inner_rate, 
#                 steps=inner_steps)
#         else:
#             rdp = np.array([_compute_rdp_4sgd(
#                 noise_multiplier=noise_multiplier, 
#                 alpha=float(alpha), 
#                 sample_rate=inner_rate, 
#                 steps=inner_steps) for alpha in alphas])
#         return rdp
    
#     else: # fedlearn setting
#         if isinstance(alphas, float):
#             rdp = _compute_rdp_4fed(noise_multiplier, alphas, inner_rate=inner_rate, inner_steps=inner_steps, outer_rate=outer_rate)
#         else:
#             rdp = np.array([_compute_rdp_4fed(noise_multiplier, float(alpha), inner_rate=inner_rate, inner_steps=inner_steps, outer_rate=outer_rate) for alpha in alphas])
        
#         return rdp * outer_rounds
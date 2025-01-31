# Copyright (C) 2019-2023, François-Guillaume Fernandez.

# This program is licensed under the Apache License 2.0.
# See LICENSE or go to <https://www.apache.org/licenses/LICENSE-2.0> for full license details.

from typing import Callable, Iterable, Optional, Tuple

import torch
from torch.optim.optimizer import Optimizer

__all__ = ["LAMB"]


class LAMB(Optimizer):
    r"""Implements the Lamb optimizer from `"Large batch optimization for deep learning: training BERT in 76 minutes"
    <https://arxiv.org/pdf/1904.00962v3.pdf>`_.

    The estimation of momentums is described as follows, :math:`\forall t \geq 1`:

    .. math::
        m_t \leftarrow \beta_1 m_{t-1} + (1 - \beta_1) g_t \\
        v_t \leftarrow \beta_2 v_{t-1} + (1 - \beta_2) g_t^2

    where :math:`g_t` is the gradient of :math:`\theta_t`,
    :math:`\beta_1, \beta_2 \in [0, 1]^3` are the exponential average smoothing coefficients,
    :math:`m_0 = 0,\ v_0 = 0`.

    Then we correct their biases using:

    .. math::
        \hat{m_t} \leftarrow \frac{m_t}{1 - \beta_1^t} \\
        \hat{v_t} \leftarrow \frac{v_t}{1 - \beta_2^t}

    And finally the update step is performed using the following rule:

    .. math::
        r_t \leftarrow \frac{\hat{m_t}}{\sqrt{\hat{v_t}} + \epsilon} \\
        \theta_t \leftarrow \theta_{t-1} - \alpha \phi(\lVert \theta_t \rVert)
        \frac{r_t + \lambda \theta_t}{\lVert r_t + \theta_t \rVert}

    where :math:`\theta_t` is the parameter value at step :math:`t` (:math:`\theta_0` being the initialization value),
    :math:`\phi` is a clipping function,
    :math:`\alpha` is the learning rate, :math:`\lambda \geq 0` is the weight decay, :math:`\epsilon > 0`.

    Args:
        params (iterable): iterable of parameters to optimize or dicts defining parameter groups
        lr (float, optional): learning rate
        betas (Tuple[float, float], optional): beta coefficients used for running averages (default: (0.9, 0.999))
        eps (float, optional): term added to the denominator to improve numerical stability (default: 1e-8)
        weight_decay (float, optional): weight decay (L2 penalty) (default: 0)
        scale_clip (tuple, optional): the lower and upper bounds for the weight norm in local LR of LARS
    """

    def __init__(
        self,
        params: Iterable[torch.nn.Parameter],
        lr: float = 1e-3,
        betas: Tuple[float, float] = (0.9, 0.999),
        eps: float = 1e-8,
        weight_decay: float = 0.0,
        scale_clip: Optional[Tuple[float, float]] = None,
    ) -> None:
        if lr < 0.0:
            raise ValueError(f"Invalid learning rate: {lr}")
        if eps < 0.0:
            raise ValueError(f"Invalid epsilon value: {eps}")
        if not 0.0 <= betas[0] < 1.0:
            raise ValueError(f"Invalid beta parameter at index 0: {betas[0]}")
        if not 0.0 <= betas[1] < 1.0:
            raise ValueError(f"Invalid beta parameter at index 1: {betas[1]}")
        defaults = dict(lr=lr, betas=betas, eps=eps, weight_decay=weight_decay)
        super().__init__(params, defaults)
        # LARS arguments
        self.scale_clip = scale_clip
        if self.scale_clip is None:
            self.scale_clip = (0.0, 10.0)

    @torch.no_grad()
    def step(self, closure: Optional[Callable[[], float]] = None) -> Optional[float]:
        """Performs a single optimization step.

        Arguments:
            closure (callable, optional): A closure that reevaluates the model and returns the loss.
        """
        loss = None
        if closure is not None:
            with torch.enable_grad():
                loss = closure()

        for group in self.param_groups:
            for p in group["params"]:
                if p.grad is None:
                    continue
                grad = p.grad.data
                if grad.is_sparse:
                    raise RuntimeError(f"{self.__class__.__name__} does not support sparse gradients")

                state = self.state[p]

                # State initialization
                if len(state) == 0:
                    state["step"] = 0
                    # Exponential moving average of gradient values
                    state["exp_avg"] = torch.zeros_like(p.data)
                    # Exponential moving average of squared gradient values
                    state["exp_avg_sq"] = torch.zeros_like(p.data)

                exp_avg, exp_avg_sq = state["exp_avg"], state["exp_avg_sq"]
                beta1, beta2 = group["betas"]

                state["step"] += 1

                # Decay the first and second moment running average coefficient
                exp_avg.mul_(beta1).add_(grad, alpha=1 - beta1)
                exp_avg_sq.mul_(beta2).addcmul_(grad, grad, value=1 - beta2)

                # Gradient term correction
                update = torch.zeros_like(p.data)
                denom = exp_avg_sq.sqrt().add_(group["eps"])
                update.addcdiv_(exp_avg, denom)

                # Weight decay
                if group["weight_decay"] != 0:
                    update.add_(p.data, alpha=group["weight_decay"])

                # LARS
                p_norm = p.data.pow(2).sum().sqrt()
                update_norm = update.pow(2).sum().sqrt()
                phi_p = p_norm.clamp(*self.scale_clip)
                # Compute the local LR
                if phi_p == 0 or update_norm == 0:
                    local_lr = 1
                else:
                    local_lr = phi_p / update_norm

                state["local_lr"] = local_lr

                p.data.add_(update, alpha=-group["lr"] * local_lr)

        return loss

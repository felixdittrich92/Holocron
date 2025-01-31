# Copyright (C) 2019-2023, François-Guillaume Fernandez.

# This program is licensed under the Apache License 2.0.
# See LICENSE or go to <https://www.apache.org/licenses/LICENSE-2.0> for full license details.

from typing import List, Optional, Tuple

from torch import nn
from torch.nn.modules.batchnorm import _BatchNorm

__all__ = ["freeze_bn", "freeze_model", "split_normalization_params"]


def freeze_bn(mod: nn.Module) -> None:
    """Prevents parameter and stats from updating in Batchnorm layers that are frozen

    >>> from holocron.models import rexnet1_0x
    >>> from holocron.trainer.utils import freeze_bn
    >>> model = rexnet1_0x()
    >>> freeze_bn(model)

    Args:
        mod (torch.nn.Module): model to train
    """

    # Loop on modules
    for m in mod.modules():
        if isinstance(m, _BatchNorm) and m.affine and all(not p.requires_grad for p in m.parameters()):
            # Switch back to commented code when https://github.com/pytorch/pytorch/issues/37823 is resolved
            m.track_running_stats = False
            m.eval()


def freeze_model(
    model: nn.Module,
    last_frozen_layer: Optional[str] = None,
    frozen_bn_stat_update: bool = False,
) -> None:
    """Freeze a specific range of model layers.

    >>> from holocron.models import rexnet1_0x
    >>> from holocron.trainer.utils import freeze_model
    >>> model = rexnet1_0x()
    >>> freeze_model(model)

    Args:
        model (torch.nn.Module): model to train
        last_frozen_layer (str, optional): last layer to freeze. Assumes layers have been registered in forward order
        frozen_bn_stat_update (bool, optional): force stats update in BN layers that are frozen
    """

    # Unfreeze everything
    for p in model.parameters():
        p.requires_grad_(True)

    # Loop on parameters
    if isinstance(last_frozen_layer, str):
        layer_reached = False
        for n, p in model.named_parameters():
            if not layer_reached or n.startswith(last_frozen_layer):
                p.requires_grad_(False)
            if n.startswith(last_frozen_layer):
                layer_reached = True
            # Once the last param of the layer is frozen, we break
            elif layer_reached:
                break
        if not layer_reached:
            raise ValueError(f"Unable to locate child module {last_frozen_layer}")

    # Loop on modules
    if not frozen_bn_stat_update:
        freeze_bn(model)


def split_normalization_params(
    model: nn.Module,
    norm_classes: Optional[List[type]] = None,
) -> Tuple[List[nn.Parameter], List[nn.Parameter]]:
    # Borrowed from https://github.com/pytorch/vision/blob/main/torchvision/ops/_utils.py
    # Adapted from https://github.com/facebookresearch/ClassyVision/blob/659d7f78/classy_vision/generic/util.py#L501
    if not norm_classes:
        norm_classes = [nn.modules.batchnorm._BatchNorm, nn.LayerNorm, nn.GroupNorm]

    for t in norm_classes:
        if not issubclass(t, nn.Module):
            raise ValueError(f"Class {t} is not a subclass of nn.Module.")

    classes = tuple(norm_classes)

    norm_params: List[nn.Parameter] = []
    other_params: List[nn.Parameter] = []
    for module in model.modules():
        if next(module.children(), None):
            other_params.extend(p for p in module.parameters(recurse=False) if p.requires_grad)
        elif isinstance(module, classes):
            norm_params.extend(p for p in module.parameters() if p.requires_grad)
        else:
            other_params.extend(p for p in module.parameters() if p.requires_grad)
    return norm_params, other_params

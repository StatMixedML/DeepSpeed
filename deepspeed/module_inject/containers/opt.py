from .base import *
from .features.meta_tensor import MetaTensorContainer
from deepspeed.model_implementations.transformers.ds_opt import DeepSpeedOPTInference
import torch
from torch.nn.parameter import Parameter
from ..policy import TransformerPolicy
from ..policy import transformer_param_names
from ..policy import maybe_copy
from ..policy import maybe_copy_qkv
from deepspeed.utils.types import ActivationFuncType


class DS_OPTContainer(MetaTensorContainer, BaseTransformerContainer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # All model specific things should be defined here instead of the base class.

    def create_module(self, config=None):
        _config = config if config is not None else self.ds_model_config
        self.module = DeepSpeedOPTInference(_config, mp_group=self.mp_group)
        self.module.config.scale_attention = self.scale_attention
        return self.module

    def load_params(self, module, sd, weight_quantizer, mp_replace, prefix):
        param_names = (
            'self_attn.q_proj.weight', \
            'self_attn.k_proj.weight', \
            'self_attn.v_proj.weight', \
            'self_attn.q_proj.bias', \
            'self_attn.k_proj.bias', \
            'self_attn.v_proj.bias', \
            'self_attn.out_proj.weight', \
            'self_attn.out_proj.bias', \
            'fc1.weight', \
            'fc1.bias', \
            'fc2.weight', \
            'fc2.bias', \
            'final_layer_norm.weight', \
            'final_layer_norm.bias', \
            'self_attn_layer_norm.weight', \
            'self_attn_layer_norm.bias'
        )

        for i in range(0, 6, 3):
            maybe_copy_qkv(module.attention,
                           sd,
                           weight_quantizer,
                           mp_replace,
                           transformer_param_names[i // 3],
                           [
                               prefix + param_names[i],
                               prefix + param_names[i + 1],
                               prefix + param_names[i + 2]
                           ],
                           split_qkv=self.split_qkv)
        for i in range(6, 8):
            maybe_copy(module.attention,
                       sd,
                       weight_quantizer,
                       mp_replace,
                       transformer_param_names[i - 4],
                       prefix + param_names[i])
        for i in range(8, 14):
            maybe_copy(module.mlp,
                       sd,
                       weight_quantizer,
                       mp_replace,
                       transformer_param_names[i - 4],
                       prefix + param_names[i])
        for i in range(14, 16):
            maybe_copy(module,
                       sd,
                       weight_quantizer,
                       mp_replace,
                       transformer_param_names[i - 4],
                       prefix + param_names[i])


class HFOPTLayerPolicy(TransformerPolicy):
    _orig_layer_class = None

    def __init__(self, client_module, inference=True, use_load_prefix=True):
        super().__init__(inference,
                         linear_layer=True,
                         mlp_act_func_type=ActivationFuncType.ReLU,
                         pre_attn_norm=True,
                         use_load_prefix=use_load_prefix)
        self.client_module = client_module

        try:
            import transformers
            HFOPTLayerPolicy._orig_layer_class = transformers.models.opt.modeling_opt.OPTDecoderLayer
            if isinstance(TransformerPolicy.hf_model_config,
                          transformers.models.opt.configuration_opt.OPTConfig):
                self.pre_attn_norm = TransformerPolicy.hf_model_config.do_layer_norm_before
        except:
            HFOPTLayerPolicy._orig_layer_class = None

    def get_hidden_heads(self):
        return self.client_module.self_attn.embed_dim, \
                self.client_module.self_attn.num_heads

    def attention(self):
        qw = self.client_module.self_attn.q_proj.weight
        qb = self.client_module.self_attn.q_proj.bias

        kw = self.client_module.self_attn.k_proj.weight
        kb = self.client_module.self_attn.k_proj.bias

        vw = self.client_module.self_attn.v_proj.weight
        vb = self.client_module.self_attn.v_proj.bias

        qkvw = Parameter(torch.cat((qw, kw, vw), dim=0), requires_grad=False)
        qkvb = Parameter(torch.cat((qb, kb, vb), dim=0), requires_grad=False)

        return qkvw, \
               qkvb, \
               self.client_module.self_attn.out_proj.weight, \
               self.client_module.self_attn.out_proj.bias

    def mlp(self):
        return self.client_module.fc1.weight, \
               self.client_module.fc1.bias, \
               self.client_module.fc2.weight, \
               self.client_module.fc2.bias

    def layernorm(self):
        return self.client_module.final_layer_norm.weight, \
               self.client_module.final_layer_norm.bias, \
               self.client_module.self_attn_layer_norm.weight, \
               self.client_module.self_attn_layer_norm.bias

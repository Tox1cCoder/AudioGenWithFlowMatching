import os
import logging
import json
import random
import torch
import torchaudio
import re

from diffusers import AutoencoderOobleck, FluxTransformer2DModel
from huggingface_hub import snapshot_download

from comfy.utils import load_torch_file, ProgressBar
import folder_paths

from tangoflux.model import TangoFlux
from .teacache import teacache_forward

log = logging.getLogger("TangoFlux")

TANGOFLUX_DIR = os.path.join(folder_paths.models_dir, "tangoflux")
if "tangoflux" not in folder_paths.folder_names_and_paths:
    current_paths = [TANGOFLUX_DIR]
else:
    current_paths, _ = folder_paths.folder_names_and_paths["tangoflux"]
folder_paths.folder_names_and_paths["tangoflux"] = (
    current_paths,
    folder_paths.supported_pt_extensions,
)
TEXT_ENCODER_DIR = os.path.join(folder_paths.models_dir, "text_encoders")


class TangoFluxLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "enable_teacache": ("BOOLEAN", {"default": False}),
                "rel_l1_thresh": (
                    "FLOAT",
                    {"default": 0.25, "min": 0.0, "max": 10.0, "step": 0.01},
                ),
            },
        }

    RETURN_TYPES = ("TANGOFLUX_MODEL", "TANGOFLUX_VAE")
    RETURN_NAMES = ("model", "vae")
    OUTPUT_TOOLTIPS = ("TangoFlux Model", "TangoFlux Vae")

    CATEGORY = "TangoFlux"
    FUNCTION = "load_tangoflux"
    DESCRIPTION = "Load TangoFlux model"

    def __init__(self):
        self.model = None
        self.vae = None
        self.enable_teacache = False
        self.rel_l1_thresh = 0.25
        self.original_forward = FluxTransformer2DModel.forward

    def load_tangoflux(
        self,
        enable_teacache=False,
        rel_l1_thresh=0.25,
        tangoflux_path=TANGOFLUX_DIR,
        text_encoder_path=TEXT_ENCODER_DIR,
        device="cuda",
    ):
        if self.model is None or self.enable_teacache != enable_teacache:

            pbar = ProgressBar(6)

            snapshot_download(
                repo_id="declare-lab/TangoFlux",
                allow_patterns=["*.json", "*.safetensors"],
                local_dir=tangoflux_path,
                local_dir_use_symlinks=False,
            )

            pbar.update(1)

            log.info("Loading config")

            with open(os.path.join(tangoflux_path, "config.json"), "r") as f:
                config = json.load(f)

            pbar.update(1)

            text_encoder = re.sub(
                r'[<>:"/\\|?*]',
                "-",
                config.get("text_encoder_name", "google/flan-t5-large"),
            )
            text_encoder_path = os.path.join(text_encoder_path, text_encoder)

            snapshot_download(
                repo_id=config.get("text_encoder_name", "google/flan-t5-large"),
                allow_patterns=["*.json", "*.safetensors", "*.model"],
                local_dir=text_encoder_path,
                local_dir_use_symlinks=False,
            )

            pbar.update(1)

            log.info("Loading TangoFlux models")

            del self.model
            self.model = None

            model_weights = load_torch_file(
                os.path.join(tangoflux_path, "tangoflux.safetensors"),
                device=torch.device(device),
            )

            pbar.update(1)

            if enable_teacache:
                log.info("Enabling TeaCache")
                FluxTransformer2DModel.forward = teacache_forward
            else:
                log.info("Disabling TeaCache")
                FluxTransformer2DModel.forward = self.original_forward

            model = TangoFlux(config=config, text_encoder_dir=text_encoder_path)

            model.load_state_dict(model_weights, strict=False)
            model.to(device)

            if enable_teacache:
                model.transformer.__class__.enable_teacache = True
                model.transformer.__class__.cnt = 0
                model.transformer.__class__.rel_l1_thresh = rel_l1_thresh
                model.transformer.__class__.accumulated_rel_l1_distance = 0
                model.transformer.__class__.previous_modulated_input = None
                model.transformer.__class__.previous_residual = None

            pbar.update(1)

            self.model = model
            del model
            self.enable_teacache = enable_teacache
            self.rel_l1_thresh = rel_l1_thresh

            if self.vae is None:
                log.info("Loading TangoFlux VAE")

                vae_weights = load_torch_file(
                    os.path.join(tangoflux_path, "vae.safetensors")
                )
                self.vae = AutoencoderOobleck()
                self.vae.load_state_dict(vae_weights)
                self.vae.to(device)

            pbar.update(1)

        if self.enable_teacache == True and self.rel_l1_thresh != rel_l1_thresh:
            self.model.transformer.__class__.rel_l1_thresh = rel_l1_thresh

            self.rel_l1_thresh = rel_l1_thresh

        return (self.model, self.vae)


class TangoFluxSampler:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("TANGOFLUX_MODEL",),
                "prompt": ("STRING", {"multiline": True, "dynamicPrompts": True}),
                "steps": ("INT", {"default": 50, "min": 1, "max": 10000, "step": 1}),
                "guidance_scale": (
                    "FLOAT",
                    {"default": 3, "min": 1, "max": 100, "step": 1},
                ),
                "duration": ("INT", {"default": 10, "min": 1, "max": 30, "step": 1}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096}),
            },
        }

    RETURN_TYPES = ("TANGOFLUX_LATENTS",)
    RETURN_NAMES = ("latents",)
    OUTPUT_TOOLTIPS = "TangoFlux Sample"

    CATEGORY = "TangoFlux"
    FUNCTION = "sample"
    DESCRIPTION = "Sampler for TangoFlux"

    def sample(
        self,
        model,
        prompt,
        steps=50,
        guidance_scale=3,
        duration=10,
        seed=0,
        batch_size=1,
        device="cuda",
    ):
        pbar = ProgressBar(steps)

        with torch.no_grad():
            model.to(device)

            try:
                if model.transformer.__class__.enable_teacache:
                    model.transformer.__class__.num_steps = steps
            except:
                pass

            log.info("Generating latents with TangoFlux")

            latents = model.inference_flow(
                prompt,
                duration=duration,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                seed=seed,
                num_samples_per_prompt=batch_size,
                callback_on_step_end=lambda: pbar.update(1),
            )

        return ({"latents": latents, "duration": duration},)


class TangoFluxVAEDecodeAndPlay:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "vae": ("TANGOFLUX_VAE",),
                "latents": ("TANGOFLUX_LATENTS",),
                "filename_prefix": ("STRING", {"default": "TangoFlux"}),
                "format": (
                    ["wav", "mp3", "flac", "aac", "wma"],
                    {"default": "wav"},
                ),
                "save_output": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ()
    OUTPUT_NODE = True

    CATEGORY = "TangoFlux"
    FUNCTION = "play"
    DESCRIPTION = "Decoder and Player for TangoFlux"

    def decode(self, vae, latents):
        results = []

        for latent in latents:
            decoded = vae.decode(latent.unsqueeze(0).transpose(2, 1)).sample.cpu()
            results.append(decoded)

        results = torch.cat(results, dim=0)

        return results

    def play(
        self,
        vae,
        latents,
        filename_prefix="TangoFlux",
        format="wav",
        save_output=True,
        device="cuda",
    ):
        audios = []
        pbar = ProgressBar(len(latents) + 2)

        if save_output:
            output_dir = folder_paths.get_output_directory()
            prefix_append = ""
            type = "output"
        else:
            output_dir = folder_paths.get_temp_directory()
            prefix_append = "_temp_" + "".join(
                random.choice("abcdefghijklmnopqrstupvxyz") for _ in range(5)
            )
            type = "temp"

        filename_prefix += prefix_append
        full_output_folder, filename, counter, subfolder, _ = (
            folder_paths.get_save_image_path(filename_prefix, output_dir)
        )

        os.makedirs(full_output_folder, exist_ok=True)

        pbar.update(1)

        duration = latents["duration"]
        latents = latents["latents"]

        vae.to(device)

        log.info("Decoding Tangoflux latents")

        waves = self.decode(vae, latents)

        pbar.update(1)

        for wave in waves:
            waveform_end = int(duration * vae.config.sampling_rate)
            wave = wave[:, :waveform_end]

            file = f"{filename}_{counter:05}_.{format}"

            torchaudio.save(
                os.path.join(full_output_folder, file), wave, sample_rate=44100
            )

            counter += 1

            audios.append({"filename": file, "subfolder": subfolder, "type": type})

            pbar.update(1)

        return {
            "ui": {"audios": audios},
        }


NODE_CLASS_MAPPINGS = {
    "TangoFluxLoader": TangoFluxLoader,
    "TangoFluxSampler": TangoFluxSampler,
    "TangoFluxVAEDecodeAndPlay": TangoFluxVAEDecodeAndPlay,
}

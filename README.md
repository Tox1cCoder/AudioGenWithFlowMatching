# Overall Pipeline

TangoFlux consists of FluxTransformer blocks, which are Diffusion Transformers (DiT) and Multimodal Diffusion Transformers (MMDiT) conditioned on a textual prompt and a duration embedding to generate a 44.1kHz audio up to 30 seconds long. TangoFlux learns a rectified flow trajectory to an audio latent representation encoded by a variational autoencoder (VAE).

![cover-photo](assets/tangoflux.png)

# Installation

```bash
pip install git+https://github.com/Tox1cCoder/AudioGenWithFlowMatching
```

Run the following command to start the web interface:

```bash
tangoflux-demo
```

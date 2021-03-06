# texturize — Copyright (c) 2020, Novelty Factory KG.  See LICENSE for details.

import os
import math

import torch
import torch.nn.functional as F
from creativeai.image.encoders import models

from .critics import GramMatrixCritic, PatchCritic, HistogramCritic
from .app import TextureSynthesizer, Application, Result
from .io import *


@torch.no_grad()
def process_iterations(
    cmd,
    log: object = None,
    size: tuple = None,
    octaves: int = None,
    variations: int = 1,
    quality: float = 2,
    model: str = "VGG11",
    layers: str = None,
    mode: str = None,
    device: str = None,
    precision: str = None,
):
    """Synthesize a new texture and return a PyTorch tensor at each iteration.
    """

    # Configure the default options dynamically, unless overriden.
    factor = math.sqrt((size[0] * size[1]) / (32 ** 2))
    octaves = octaves or getattr(cmd, "octaves", int(math.log(factor, 2) + 1.0))

    # Setup the application to use throughout the synthesis.
    app = Application(log, device, precision)

    # Encoder used by all the critics at every octave.
    encoder = getattr(models, model)(pretrained=True, pool_type=torch.nn.AvgPool2d)
    encoder = encoder.to(device=app.device, dtype=app.precision)
    app.encoder = encoder
    app.layers = layers
    app.mode = mode

    # Coarse-to-fine rendering, number of octaves specified by user.
    seed = None
    for octave, scale in enumerate(2 ** s for s in range(octaves - 1, -1, -1)):
        app.log.info(f"\n OCTAVE #{octave} ")
        app.log.debug("<- scale:", f"1/{scale}")

        app.progress = app.log.create_progress_bar(100)

        result_size = (variations, 3, size[1] // scale, size[0] // scale)
        app.log.debug("<- seed:", tuple(result_size[2:]))

        for dtype in [torch.float32, torch.float16]:
            if app.precision != dtype:
                app.precision = dtype
                app.encoder = app.encoder.to(dtype=dtype)
                if seed is not None:
                    seed = seed.to(app.device)

            try:
                critics = cmd.prepare_critics(app, scale)
                seed = cmd.prepare_seed_tensor(app, result_size, previous=seed)

                for result in app.process_octave(
                    seed, app.encoder, critics, octave, scale, quality=quality,
                ):
                    yield result

                seed = result.images
                del result
                break

            except RuntimeError as e:
                if "CUDA out of memory." not in str(e):
                    raise

                import gc; gc.collect
                torch.cuda.empty_cache()


@torch.no_grad()
def process_octaves(cmd, **kwargs):
    """Synthesize a new texture from sources and return a PyTorch tensor at each octave.
    """
    for r in process_iterations(cmd, **kwargs):
        if r.iteration >= 0:
            continue

        yield Result(
            r.images, r.octave, r.scale, -r.iteration, r.loss, r.rate, r.retries
        )


def process_single_command(cmd, log: object, output: str = None, **config: dict):
    for result in process_octaves(cmd, log=log, **config):
        result = cmd.finalize_octave(result)

        images = save_tensor_to_images(result.images)
        filenames = []
        for i, image in enumerate(images):
            # Save the files for each octave to disk.
            filename = output.format(
                octave=result.octave,
                variation=f"_{i}" if len(images) > 1 else "",
                command=cmd.__class__.__name__.lower(),
            )
            image.resize(size=config["size"], resample=0).save(
                filename, lossless=True, compress=6
            )
            log.debug("\n=> output:", filename)
            filenames.append(filename)

    return result, filenames

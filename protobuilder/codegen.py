import os
import docker
import logging

_LOGGER = logging.getLogger(__name__)


def codegen(config, services_dir, output_dir):
    client = docker.from_env()
    os.makedirs(output_dir, 0o755, exist_ok=True)
    volumes = {
        services_dir: {'bind': '/defs', 'mode': 'rw'},
        output_dir: {'bind': '/out', 'mode': 'rw'},
    }
    c = client.containers.run(
        image='scottschroeder/protoc-all:1.11',  # TODO Image src
        command=['-d', config['service'], '-l', config['lang'], '-o', '/out'],
        volumes=volumes,
        auto_remove=True,
    )
    _LOGGER.debug("Build Results: %s", c)

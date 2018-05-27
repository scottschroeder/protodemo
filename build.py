#!/usr/bin/env python3

import argparse
import logging
import json
import jsonschema
import docker
import pygit2
import copy
import os
import sys
import shutil

_LOGGER = logging.getLogger(__name__)

_SERVICE_CONFIG_NAME = "config.json"
_SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
_SERVICES = os.path.join(_SCRIPT_PATH, 'service')
_DEFAULT_CONFIG = os.path.join(_SCRIPT_PATH, 'config.json')


class ProtoRepoException(Exception):
    """Generic exception for this module"""


class BadConfig(ProtoRepoException):
    """A config was not in the right format"""


_DEFAULT_CONFIG_SCHEMA = {"type": "object"}

_SERVICE_CONFIG_SCHEMA = {
    "type": "array",
    "items": {"type": "object"}
}

_FULL_CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "github_org": {"type": "string"},
        "repo_name": {"type": "string"},
        "lang": {
            "type": "string",
            "enum": ["python", "go"]
        }
    },
    "required": ["lang", "github_org"]
}


def _setup_logging(verbosity=0):
    if verbosity == 0:
        loglevel = logging.WARNING
    elif verbosity == 1:
        loglevel = logging.INFO
    else:
        loglevel = logging.DEBUG
    # Set up logging
    logging_format = "[%(levelname)s] - %(message)s"
    logging.basicConfig(stream=sys.stderr, level=loglevel, format=logging_format)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate gRPC Stubs")
    parser.add_argument("--verbose", "-v", action='count', default=0, help="-v for info, -vv for debug")
    parser.add_argument("--service", "-s", action='store', help="Apply only to a specific service")
    parser.add_argument("--config", "-c", action='store', default=_DEFAULT_CONFIG, help="Default configuration file")
    args = parser.parse_args()
    return args


def list_services():
    return os.listdir(_SERVICES)


def load_default_config(path):
    with open(path) as f:
        data = json.load(f)
        jsonschema.validate(data, _DEFAULT_CONFIG_SCHEMA)
        return data


def load_service_config(service, default_config):
    path = os.path.join(_SERVICES, service, _SERVICE_CONFIG_NAME)
    try:
        with open(path) as f:
            service_all_langs_config = json.load(f)
    except FileNotFoundError:
        raise BadConfig("Must define a config file for each service, missing: {}".format(path))

    jsonschema.validate(service_all_langs_config, _SERVICE_CONFIG_SCHEMA)

    config = []
    for service_lang_config in service_all_langs_config:
        full_config = copy.deepcopy(default_config)
        full_config.update(service_lang_config)
        jsonschema.validate(full_config, _FULL_CONFIG_SCHEMA)

        full_config.update({'path': os.path.join(_SERVICES, service)})
        if 'repo' not in full_config:
            full_config.update({'repo': 'proto-{}-{}'.format(service, full_config['lang'])})
        full_config.update({'service': service})
        config.append(full_config)
    return config


def codegen(config):
    client = docker.from_env()
    output_dir = os.path.join(_SCRIPT_PATH, 'out', config['repo'])
    os.makedirs(output_dir, 0o755, exist_ok=True)
    volumes = {
        _SERVICES: {'bind': '/defs', 'mode': 'rw'},
        output_dir: {'bind': '/out', 'mode': 'rw'},
    }
    c = client.containers.run(
        image='scottschroeder/protoc-all:1.11',
        command=['-d', config['service'], '-l', config['lang'], '-o', '/out'],
        volumes=volumes,
        auto_remove=True,
    )
    _LOGGER.debug("Build Results: %s", c)
    return output_dir


def do_job(config):
    _LOGGER.info("Processing {} ({}):\n{}".format(
        config['service'], config['lang'],
        json.dumps(config, indent=2, sort_keys=True)
    ))
    code_dir = codegen(config)
    update_repo(config, code_dir)


def wipe_git_repo(repo_dir):
    for fs_object in os.listdir(repo_dir):
        if fs_object == '.git':
            continue
        shutil.rmtree(os.path.join(repo_dir, fs_object))


def update_repo(config, code_dir):
    repo_dir = os.path.join(_SCRIPT_PATH, 'repo', config['repo'])
    repo = pygit2.clone_repository(
        '{}/{}'.format(config['github_org'], config['repo']),
        repo_dir,
    )

    # TODO author and committer and branch
    author = pygit2.Signature('root (author)', 'root@localhost')
    committer = pygit2.Signature('root (committer)', 'root@localhost')
    branch = 'refs/heads/master'

    # Check that its a real repository
    try:
        repo.revparse_single("HEAD")
    except KeyError:
        tree = repo.TreeBuilder().write()
        oid = repo.create_commit(branch, author, committer, 'Initial Commit', tree, [])
        repo.head.set_target(oid)
        _LOGGER.info("Initialized Repository %s at %s", config['repo'], oid.hex)

    wipe_git_repo(repo_dir)

    _LOGGER.debug("Copy %s -> %s", code_dir, repo_dir)
    shutil.copytree(code_dir, os.path.join(repo_dir, 'src'))
    repo.index.add_all()
    repo.index.write()

    diff = repo.diff('HEAD', cached=True)
    if len(diff) == 0:
        _LOGGER.debug("No changes for %s", config['repo'])
        return

    tree = repo.index.write_tree()
    oid = repo.create_commit(
        branch,
        author,
        committer,
        'Automatic Commit - protoc codegen',
        tree,
        [repo.head.target]
    )
    repo.head.set_target(oid)
    _LOGGER.info("Made Commit to Repository %s at %s", config['repo'], oid.hex)

    repo.remotes.set_push_url('origin', repo.remotes['origin'].url)
    repo.remotes['origin'].push([branch])


def remote_data(remote):
    return {
        'name': remote.name,
        'url': remote.url,
        'push_url': remote.push_url,
    }


def main():
    args = parse_args()
    _setup_logging(args.verbose)
    _LOGGER.debug(args)

    manifest = []
    try:
        default_config = load_default_config(args.config)
        services = list_services()
        for service in services:
            config = load_service_config(service, default_config)
            manifest.extend(config)
        for job in manifest:
            # if job['service'] == 'helloworld' and job['lang'] == 'python':
            do_job(job)
    except ProtoRepoException as e:
        _LOGGER.error(e)
        sys.exit(1)
    except Exception:
        _LOGGER.exception("Fatal exception running protorepo build job!")
        sys.exit(1)


if __name__ == "__main__":
    main()

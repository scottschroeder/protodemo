#!/usr/bin/env python3

import argparse
import logging
import pprint
import json
import pygit2
import os
import sys
import tempfile
from cookiecutter.main import cookiecutter

from protobuilder import (
    ProtoRepoException,
    codegen,
    config,
    fsutils,
    gitutils,
)

_LOGGER = logging.getLogger(__name__)

_RELATIVE_PATH_TO_SERVICES = 'service'
_RELATIVE_PATH_TO_CONFIG = 'config.json'
_RELATIVE_PATH_TO_TEMPLATES = 'lang'


def _setup_logging(verbosity=0):
    if verbosity == 0:
        loglevel = logging.WARNING
    elif verbosity == 1:
        loglevel = logging.INFO
    else:
        loglevel = logging.DEBUG
    # Set up logging
    logging_format = "[%(levelname)s] (%(name)s) - %(message)s"
    logging.basicConfig(stream=sys.stderr, level=loglevel, format=logging_format)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate gRPC Stubs")
    parser.add_argument("--verbose", "-v", action='count', default=0, help="-v for info, -vv for debug")
    parser.add_argument("--service", "-s", action='store', help="Apply only to a specific service")
    parser.add_argument("--lang", "-l", action='store', help="Apply only to a specific service")
    parser.add_argument("--config", "-c", action='store', help="Default configuration file")
    parser.add_argument("--git", action='store_true', help="Apply Changes to existing git repositories")
    parser.add_argument("--output", "-o", action='store', help="Output directory to store codegen")
    parser.add_argument("repo", action='store', help="Location of protorepo")
    args = parser.parse_args()
    return args


def update_repo(job_config, service_dir, output_dir, templates_dir, update_git=False):
    _LOGGER.info("Processing {} ({}):\n{}".format(
        job_config['service'], job_config['lang'],
        json.dumps(job_config, indent=2, sort_keys=True)
    ))

    repo_dir = os.path.join(output_dir, job_config['repo'])

    if update_git:
        repo = pygit2.clone_repository(
            '{}/{}'.format(job_config['github_org'], job_config['repo']),
            repo_dir,
        )
    else:
        repo = pygit2.init_repository(repo_dir)

    fsutils.wipe_git_repo(repo_dir)

    cookiecutter(
        os.path.join(templates_dir, "cookiecutter-{}".format(job_config['lang'])),
        output_dir=os.path.split(repo_dir)[0],
        extra_context=job_config,
        no_input=True,
        overwrite_if_exists=True,
    )

    # TODO author and committer and branch
    author = pygit2.Signature('root (author)', 'root@localhost')
    committer = pygit2.Signature('root (committer)', 'root@localhost')
    branch = 'refs/heads/master'

    if repo.is_empty:
        tree = repo.TreeBuilder().write()
        oid = repo.create_commit(branch, author, committer, 'Initial Commit', tree, [])
        repo.head.set_target(oid)
        _LOGGER.info("Initialized Repository %s at %s", job_config['repo'], oid.hex)

    codegen.codegen(job_config, service_dir, os.path.join(repo_dir, job_config['source_dir']))
    repo.index.add_all()
    repo.index.write()

    diff = repo.diff('HEAD', cached=True)
    if len(diff) == 0:
        _LOGGER.debug("No changes for %s", job_config['repo'])
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
    _LOGGER.info("Made Commit to Repository %s at %s", job_config['repo'], oid.hex)

    if update_git:
        repo.remotes.set_push_url('origin', repo.remotes['origin'].url)
        repo.remotes['origin'].push([branch])


def main():
    args = parse_args()
    _setup_logging(args.verbose)
    _LOGGER.debug(args)

    manifest = []

    repo = gitutils.get_repo_from_path(args.repo)
    working_dir = repo.workdir
    service_dir = os.path.join(working_dir, _RELATIVE_PATH_TO_SERVICES)

    repodata = gitutils.analyze_head(repo)
    _LOGGER.debug("Repo Data: %s", repodata)

    if not args.output:
        tempdir = tempfile.TemporaryDirectory()
        output_dir = tempdir.name
    else:
        tempdir = None
        output_dir = os.path.abspath(args.output)

    try:
        config_path = args.config if args.config else os.path.join(working_dir, _RELATIVE_PATH_TO_CONFIG)
        default_config = config.load_default_config(config_path)
        for service in os.listdir(service_dir):
            service_configs = config.load_service_config(service, default_config, service_dir)
            manifest.extend(service_configs)
        for job in manifest:
            if args.lang and args.lang != job['lang']:
                continue
            if args.service and args.service != job['service']:
                continue

            update_repo(
                job,
                service_dir,
                output_dir,
                os.path.join(working_dir, _RELATIVE_PATH_TO_TEMPLATES),
                update_git=args.git
            )


    except ProtoRepoException as e:
        _LOGGER.error(e)
        sys.exit(1)
    except Exception:
        _LOGGER.exception("Fatal exception running protorepo build job!")
        sys.exit(1)
    finally:
        if tempdir is not None:
            _LOGGER.debug("Destroying temp dir: %s", output_dir)
            tempdir.cleanup()


if __name__ == "__main__":
    main()

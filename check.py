#!/usr/bin/env python3

import argparse
import base64
import logging
import json
import datetime
import jsonschema
import docker
import pygit2
import copy
import os
import sys
import shutil
from cookiecutter.main import cookiecutter

_LOGGER = logging.getLogger(__name__)

_SERVICE_CONFIG_NAME = "config.json"
_SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
_SERVICES = os.path.join(_SCRIPT_PATH, 'service')
_DEFAULT_CONFIG = os.path.join(_SCRIPT_PATH, 'config.json')
_TEMPLATES = os.path.join(_SCRIPT_PATH, 'lang')


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
        "repo": {"type": "string"},
        "lang": {
            "type": "string",
            "enum": ["python", "go"]
        }
    },
    "required": ["lang", "github_org"]
}


def generated_source_dir(lang, service):
    if lang == 'python':
        return "{}_proto".format(service)
    elif lang == 'go':
        return "{}_proto".format(service)
    else:
        raise ProtoRepoException("Tried to figure out source for unknown lang: {}".format(lang))


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

        if 'repo' not in full_config:
            full_config.update({'repo': 'proto-{}-{}'.format(service, full_config['lang'])})
        full_config.update({
            'path': os.path.join(_SERVICES, service),
            'service': service,
            'source_dir': generated_source_dir(full_config['lang'], service)
        })
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
        full_path = os.path.join(repo_dir, fs_object)
        if fs_object == '.git':
            continue
        elif os.path.isfile(full_path):
            os.remove(full_path)
        else:
            shutil.rmtree(full_path)


def copy_source_code(src, dst):
    if not os.path.exists(dst):
        os.mkdir(dst, 0o755)

    for fs_object in os.listdir(src):
        src_full_path = os.path.join(src, fs_object)
        dst_full_path = os.path.join(dst, fs_object)

        if os.path.isdir(src_full_path):
            shutil.copytree(src_full_path, dst_full_path)
        else:
            shutil.copy2(src_full_path, dst_full_path)


def make_cookiecutter(template, dest, config):
    cookiecutter(template, output_dir=dest, no_input=True, extra_context=config, overwrite_if_exists=True)


def update_repo(config, code_dir):
    repo_dir = os.path.join(_SCRIPT_PATH, 'repo', config['repo'])
    repo = pygit2.clone_repository(
        '{}/{}'.format(config['github_org'], config['repo']),
        repo_dir,
    )
    wipe_git_repo(repo_dir)

    make_cookiecutter(
        os.path.join(_TEMPLATES, "cookiecutter-{}".format(config['lang'])),
        os.path.split(repo_dir)[0],
        config,
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

    copy_source_code(code_dir, os.path.join(repo_dir, config['source_dir']))
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


def get_protorepo():
    repository_path = pygit2.discover_repository(_SCRIPT_PATH)
    repo = pygit2.Repository(repository_path)
    return repo


def gettag(repo, sha):
    tag = repo[sha]
    print("oid: {}".format(sha))
    print("obj: {}".format(tag))
    print("isinstance(tag, pygit2.Tag) -> {}".format(isinstance(tag, pygit2.Tag)))
    print("pygit2.GIT_OBJ_TAG == tag.type -> {}".format(pygit2.GIT_OBJ_TAG == tag.type))


def repo_data(repo):
    objects = {
        'tags': [],
        'commits': [],
    }

    for objhex in repo:
        obj = repo[objhex]
        if obj.type == pygit2.GIT_OBJ_COMMIT:
            objects['commits'].append({
                'hash': obj.hex,
                'message': obj.message,
                'commit_date': obj.commit_time,
                'author_name': obj.author.name,
                'author_email': obj.author.email,
                'parents': [c.hex for c in obj.parents],
            })
        elif obj.type == pygit2.GIT_OBJ_TAG:
            objects['tags'].append({
                'hex': obj.hex,
                'name': obj.name,
                'message': obj.message,
                'target': str(obj.target),
                'tagger_name': obj.tagger.name,
                'tagger_email': obj.tagger.email,
            })
        else:
            # ignore blobs and trees
            pass

    return objects


def analyze_head(repo):
    data = {
        'committer': repo.default_signature,
    }
    head_ref = repo.head.name
    short_ref = repo.head.shorthand

    if head_ref.startswith('refs/heads/'):
        data['branch'] = short_ref
        data['branch_ref'] = head_ref
    else:
        _LOGGER.warning("Could not figure out branch from %s -> %s", head_ref, short_ref)

    commit = repo.get(repo.head.target)
    data['author'] = commit.author
    data['message'] = commit.message

    data['tags'] = [
        analyze_tag(repo, tagref)
        for tagref in get_all_tags(repo, repo.head.target)
    ]

    return data


def analyze_tag(repo, tagref):
    """
    Return a dictionary containing protorepo relevant data from a specific tagref
    e.g. 'refs/tags/helloworld/1.0.0'
    """

    try:
        tagname = tagref.split('refs/tags/')[1]
    except IndexError:
        raise ValueError("tagref '{}' was not of the form 'refs/tags/mytag'".format(tagref))

    _LOGGER.info("Tagname: %s", tagname)

    tagobj = repo.get(repo.lookup_reference(tagref).target)

    if isinstance(tagobj, pygit2.Tag):
        _LOGGER.debug("Tag object %s is an annotated tag", tagobj)
        tagger = tagobj.tagger
        message = tagobj.message
    else:
        _LOGGER.debug("Tag object %s is a lightweight tag, using commit data", tagobj)
        tagger = repo.get(repo.head.target).author
        message = "{}\n".format(tagname)

    data = {
        'name': tagname,
        'tag_ref': tagref,
        'tagger': tagger,
        'message': message,
    }

    tag_parts = tagname.split('/')
    if len(tag_parts) == 2:
        data['service'] = tag_parts[0]
        data['version'] = tag_parts[1]
        if tag_parts[0] not in list_services():
            _LOGGER.warning("Did not recognize service %s from tag %s", data['service'], tagname)

    return data


def get_all_tags(repo, target):
    """
    All the possible tags which describe a particular commit
    get_all_tags(repo, repo.head.target) -> ['refs/tags/1.0.0.beta', 'refs/tags/1.0.0']
    """
    return [
        ref
        for ref in repo.listall_references()
        if ref.startswith('refs/tags/') and get_target_from_tagref(repo, ref) == target
    ]


def get_target_from_tagref(repo, tagref):
    """
    Resolve both lightweight and annotated tags to a commit target
    """
    target = repo.lookup_reference(tagref).target
    tagobj = repo.get(target)
    if isinstance(tagobj, pygit2.Tag):
        return tagobj.target
    else:
        return target


def main():
    args = parse_args()
    _setup_logging(args.verbose)
    _LOGGER.debug(args)

    manifest = []

    repo = get_protorepo()
    data = analyze_head(repo)
    import pprint
    pprint.pprint(data)
    return
    print("name: {}, shorthand: {}".format(repo.head.name, repo.head.shorthand))
    try:
        tagname = repo.describe(describe_strategy=pygit2.GIT_DESCRIBE_TAGS)
        tagobj = repo.lookup_reference('refs/tags/{}'.format(tagname))
        gettag(repo, tagobj.target)
        print(tagobj, tagobj.target)
    except KeyError:
        tagname = None
        tagobj = None

    print(tagname, tagobj)
    return

    try:
        default_config = load_default_config(args.config)
        services = list_services()
        for service in services:
            config = load_service_config(service, default_config)
            manifest.extend(config)
        for job in manifest:
            # if job['lang'] == 'python':
            do_job(job)
    except ProtoRepoException as e:
        _LOGGER.error(e)
        sys.exit(1)
    except Exception:
        _LOGGER.exception("Fatal exception running protorepo build job!")
        sys.exit(1)


if __name__ == "__main__":
    main()

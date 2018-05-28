import json
import os
import copy

import jsonschema

from protobuilder import ProtoRepoException


class BadConfig(ProtoRepoException):
    """A config was not in the right format"""


_SERVICE_CONFIG_NAME = "config.json"
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


def load_default_config(path):
    with open(path) as f:
        data = json.load(f)
        jsonschema.validate(data, _DEFAULT_CONFIG_SCHEMA)
        return data


def generated_source_dir(lang, service):
    if lang == 'python':
        return "{}_proto".format(service)
    elif lang == 'go':
        return "{}_proto".format(service)
    else:
        raise ProtoRepoException("Tried to figure out source for unknown lang: {}".format(lang))


def load_service_config(service, default_config, services_dir):
    path = os.path.join(services_dir, service, _SERVICE_CONFIG_NAME)
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
            'path': os.path.join(services_dir, service),
            'service': service,
            'source_dir': generated_source_dir(full_config['lang'], service)
        })
        config.append(full_config)
    return config

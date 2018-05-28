import pygit2
import logging

_LOGGER = logging.getLogger(__name__)


def get_repo_from_path(path):
    repository_path = pygit2.discover_repository(path)
    return pygit2.Repository(repository_path)


def repo_data(repo):
    """
    This is a helper utility that doesn't get used by the main execution
    Its been invaluable enough times that it gets to stay
    """
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


def jsonify_git_data(data):
    pretty = {}

    for k, v in data.items():
        if isinstance(v, pygit2.Signature):
            pretty.update({k: "{} <{}>".format(v.name, v.email)})
        elif isinstance(v, dict):
            pretty.update({k: jsonify_git_data(v)})
        elif isinstance(v, list):
            pretty.update({k: [jsonify_git_data(item) for item in v]})
        else:
            pretty.update({k: v})

    return pretty


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
    data['dirty'] = check_dirty(repo)

    data['tags'] = [
        analyze_tag(repo, tagref)
        for tagref in get_all_tags(repo, repo.head.target)
    ]

    return data


def check_dirty(repo):
    working = repo.diff()
    last_commit = repo.diff('HEAD')
    staged = repo.diff('HEAD', cached=True)

    if len(staged) + len(working) + len(last_commit):
        return True
    else:
        return False


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

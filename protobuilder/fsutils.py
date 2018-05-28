import os
import shutil

def wipe_git_repo(repo_dir):
    for fs_object in os.listdir(repo_dir):
        full_path = os.path.join(repo_dir, fs_object)
        if fs_object == '.git':
            continue
        elif os.path.isfile(full_path):
            os.remove(full_path)
        else:
            shutil.rmtree(full_path)


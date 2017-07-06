import os
import tempfile

from ast2vec import ensure_bblfsh_is_running_noexc, install_enry, setup_logging


ENRY = None


def setup():
    setup_logging("INFO")
    global ENRY
    if ENRY is not None:
        return
    ENRY = os.path.join(tempfile.mkdtemp(), "enry")
    install_enry(target=ENRY)
    ensure_bblfsh_is_running_noexc()

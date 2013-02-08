def isearch_run(query):
    """
    Interactive history search that updated as you type.

    The query for this program is the same as the one for
    ``rash search`` command.

    You need percol_ to use this command.

    _percol: https://github.com/mooz/percol

    If you use zsh, you can setup a keybind like this to quickly
    launch iserch and execute the result.::

      # Type `Ctrl-x r` to start isearch
      bindkey "^Xr" rash-zle-isearch

    If you like command or you are not using zsh, you can add
    something like the following in your rc file to start and
    execute the chosen command.

      rash-isearch(){
        eval "$(rash isearch)"
      }

    """
    from .config import ConfigStore
    from .interactive_search import launch_isearch
    launch_isearch(ConfigStore(), query=query)


def isearch_add_arguments(parser):
    parser.add_argument(
        'query', nargs='?', default='--cwd . ',
        help='default query')


commands = [
    ('isearch', isearch_add_arguments, isearch_run),
]
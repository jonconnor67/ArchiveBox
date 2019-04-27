__package__ = 'archivebox'

import re
import os
import sys
import shutil

from typing import Dict, List, Optional, Set, Tuple, Iterable, IO

from crontab import CronTab, CronSlices

from .cli import (
    list_subcommands,
    run_subcommand,
    display_first,
    meta_cmds,
    main_cmds,
    archive_cmds,
)
from .index.schema import Link
from .util import (
    enforce_types,
    TimedProgress,
    get_dir_size,
    human_readable_size,
    save_stdin_to_sources,
    save_file_to_sources,
    links_to_csv,
    to_json,
    folders_to_str,
)
from .index import (
    links_after_timestamp,
    load_main_index,
    import_new_links,
    write_main_index,
    link_matches_filter,
    get_indexed_folders,
    get_archived_folders,
    get_unarchived_folders,
    get_present_folders,
    get_valid_folders,
    get_invalid_folders,
    get_duplicate_folders,
    get_orphaned_folders,
    get_corrupted_folders,
    get_unrecognized_folders,
    fix_invalid_folder_locations,
)
from .index.json import (
    parse_json_main_index,
    parse_json_links_details,
)
from .index.sql import parse_sql_main_index, get_admins, apply_migrations
from .index.html import parse_html_main_index
from .extractors import archive_link
from .config import (
    stderr,
    ConfigDict,
    ANSI,
    IS_TTY,
    USER,
    ARCHIVEBOX_BINARY,
    ONLY_NEW,
    OUTPUT_DIR,
    SOURCES_DIR,
    ARCHIVE_DIR,
    LOGS_DIR,
    CONFIG_FILE,
    ARCHIVE_DIR_NAME,
    SOURCES_DIR_NAME,
    LOGS_DIR_NAME,
    STATIC_DIR_NAME,
    JSON_INDEX_FILENAME,
    HTML_INDEX_FILENAME,
    SQL_INDEX_FILENAME,
    ROBOTS_TXT_FILENAME,
    FAVICON_FILENAME,
    check_dependencies,
    check_data_folder,
    write_config_file,
    setup_django,
    VERSION,
    CODE_LOCATIONS,
    EXTERNAL_LOCATIONS,
    DATA_LOCATIONS,
    DEPENDENCIES,
    load_all_config,
    CONFIG,
    USER_CONFIG,
    get_real_name,
)
from .cli.logging import (
    log_archiving_started,
    log_archiving_paused,
    log_archiving_finished,
    log_removal_started,
    log_removal_finished,
    log_list_started,
    log_list_finished,
)


ALLOWED_IN_OUTPUT_DIR = {
    '.DS_Store',
    '.venv',
    'venv',
    'virtualenv',
    '.virtualenv',
    ARCHIVE_DIR_NAME,
    SOURCES_DIR_NAME,
    LOGS_DIR_NAME,
    STATIC_DIR_NAME,
    SQL_INDEX_FILENAME,
    JSON_INDEX_FILENAME,
    HTML_INDEX_FILENAME,
    ROBOTS_TXT_FILENAME,
    FAVICON_FILENAME,
}

def help(out_dir: str=OUTPUT_DIR) -> None:
    all_subcommands = list_subcommands()
    COMMANDS_HELP_TEXT = '\n    '.join(
        f'{cmd.ljust(20)} {summary}'
        for cmd, summary in all_subcommands.items()
        if cmd in meta_cmds
    ) + '\n\n    ' + '\n    '.join(
        f'{cmd.ljust(20)} {summary}'
        for cmd, summary in all_subcommands.items()
        if cmd in main_cmds
    ) + '\n\n    ' + '\n    '.join(
        f'{cmd.ljust(20)} {summary}'
        for cmd, summary in all_subcommands.items()
        if cmd in archive_cmds
    ) + '\n\n    ' + '\n    '.join(
        f'{cmd.ljust(20)} {summary}'
        for cmd, summary in all_subcommands.items()
        if cmd not in display_first
    )


    if os.path.exists(os.path.join(out_dir, JSON_INDEX_FILENAME)):
        print('''{green}ArchiveBox v{}: The self-hosted internet archive.{reset}

{lightred}Active data directory:{reset}
    {}

{lightred}Usage:{reset}
    archivebox [command] [--help] [--version] [...args]

{lightred}Commands:{reset}
    {}

{lightred}Example Use:{reset}
    mkdir my-archive; cd my-archive/
    archivebox init
    archivebox info

    archivebox add https://example.com/some/page
    archivebox add --depth=1 ~/Downloads/bookmarks_export.html
    
    archivebox list --sort=timestamp --csv=timestamp,url,is_archived
    archivebox schedule --every=week https://example.com/some/feed.rss
    archivebox update --resume=15109948213.123

{lightred}Documentation:{reset}
    https://github.com/pirate/ArchiveBox/wiki
'''.format(VERSION, out_dir, COMMANDS_HELP_TEXT, **ANSI))
    
    else:
        print('{green}Welcome to ArchiveBox v{}!{reset}'.format(VERSION, **ANSI))
        print()
        print('To import an existing archive (from a previous version of ArchiveBox):')
        print('    1. cd into your data dir OUTPUT_DIR (usually ArchiveBox/output) and run:')
        print('    2. archivebox init')
        print()
        print('To start a new archive:')
        print('    1. Create an empty directory, then cd into it and run:')
        print('    2. archivebox init')
        print()
        print('For more information, see the documentation here:')
        print('    https://github.com/pirate/ArchiveBox/wiki')


def version(quiet: bool=False, out_dir: str=OUTPUT_DIR) -> None:
    if quiet:
        print(VERSION)
    else:
        print('ArchiveBox v{}'.format(VERSION))
        print()

        print('{white}[i] Dependency versions:{reset}'.format(**ANSI))
        for name, dependency in DEPENDENCIES.items():
            print_dependency_version(name, dependency)
        
        print()
        print('{white}[i] Code locations:{reset}'.format(**ANSI))
        for name, folder in CODE_LOCATIONS.items():
            print_folder_status(name, folder)

        print()
        print('{white}[i] External locations:{reset}'.format(**ANSI))
        for name, folder in EXTERNAL_LOCATIONS.items():
            print_folder_status(name, folder)

        print()
        print('{white}[i] Data locations:{reset}'.format(**ANSI))
        for name, folder in DATA_LOCATIONS.items():
            print_folder_status(name, folder)

        print()
        check_dependencies()


def run(subcommand: str, subcommand_args: Optional[List[str]], stdin: Optional[IO]=None, out_dir: str=OUTPUT_DIR) -> None:
    run_subcommand(
        subcommand=subcommand,
        subcommand_args=subcommand_args,
        stdin=stdin,
        out_dir=out_dir,
    )


def init(out_dir: str=OUTPUT_DIR) -> None:
    os.makedirs(out_dir, exist_ok=True)

    is_empty = not len(set(os.listdir(out_dir)) - ALLOWED_IN_OUTPUT_DIR)
    existing_index = os.path.exists(os.path.join(out_dir, JSON_INDEX_FILENAME))

    if is_empty and not existing_index:
        print('{green}[+] Initializing a new ArchiveBox collection in this folder...{reset}'.format(**ANSI))
        print(f'    {out_dir}')
        print('{green}------------------------------------------------------------------{reset}'.format(**ANSI))
    elif existing_index:
        print('{green}[*] Updating existing ArchiveBox collection in this folder...{reset}'.format(**ANSI))
        print(f'    {out_dir}')
        print('{green}------------------------------------------------------------------{reset}'.format(**ANSI))
    else:
        stderr(
            ("{red}[X] This folder appears to already have files in it, but no index.json is present.{reset}\n\n"
            "    You must run init in a completely empty directory, or an existing data folder.\n\n"
            "    {lightred}Hint:{reset} To import an existing data folder make sure to cd into the folder first, \n"
            "    then run and run 'archivebox init' to pick up where you left off.\n\n"
            "    (Always make sure your data folder is backed up first before updating ArchiveBox)"
            ).format(out_dir, **ANSI)
        )
        raise SystemExit(1)

    if existing_index:
        print('\n{green}[*] Verifying archive folder structure...{reset}'.format(**ANSI))
    else:
        print('\n{green}[+] Building archive folder structure...{reset}'.format(**ANSI))
    
    os.makedirs(SOURCES_DIR, exist_ok=True)
    print(f'    √ {SOURCES_DIR}')
    
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    print(f'    √ {ARCHIVE_DIR}')

    os.makedirs(LOGS_DIR, exist_ok=True)
    print(f'    √ {LOGS_DIR}')

    write_config_file({}, out_dir=out_dir)
    print(f'    √ {CONFIG_FILE}')
    
    if os.path.exists(os.path.join(out_dir, SQL_INDEX_FILENAME)):
        print('\n{green}[*] Verifying main SQL index and running migrations...{reset}'.format(**ANSI))
    else:
        print('\n{green}[+] Building main SQL index and running migrations...{reset}'.format(**ANSI))
    
    setup_django(out_dir, check_db=False)
    from django.conf import settings
    assert settings.DATABASE_FILE == os.path.join(out_dir, SQL_INDEX_FILENAME)
    print(f'    √ {settings.DATABASE_FILE}')
    print()
    for migration_line in apply_migrations(out_dir):
        print(f'    {migration_line}')


    assert os.path.exists(settings.DATABASE_FILE)
    
    # from django.contrib.auth.models import User
    # if IS_TTY and not User.objects.filter(is_superuser=True).exists():
    #     print('{green}[+] Creating admin user account...{reset}'.format(**ANSI))
    #     call_command("createsuperuser", interactive=True)

    print()
    print('{green}[*] Collecting links from any existing indexes and archive folders...{reset}'.format(**ANSI))

    all_links: Dict[str, Link] = {}
    if existing_index:
        all_links = {
            link.url: link
            for link in load_main_index(out_dir=out_dir, warn=False)
        }
        print('    √ Loaded {} links from existing main index.'.format(len(all_links)))

    # Links in data folders that dont match their timestamp
    fixed, cant_fix = fix_invalid_folder_locations(out_dir=out_dir)
    if fixed:
        print('    {lightyellow}√ Fixed {} data directory locations that didn\'t match their link timestamps.{reset}'.format(len(fixed), **ANSI))
    if cant_fix:
        print('    {lightyellow}! Could not fix {} data directory locations due to conflicts with existing folders.{reset}'.format(len(cant_fix), **ANSI))

    # Links in JSON index but not in main index
    orphaned_json_links = {
        link.url: link
        for link in parse_json_main_index(out_dir)
        if link.url not in all_links
    }
    if orphaned_json_links:
        all_links.update(orphaned_json_links)
        print('    {lightyellow}√ Added {} orphaned links from existing JSON index...{reset}'.format(len(orphaned_json_links), **ANSI))

    # Links in SQL index but not in main index
    orphaned_sql_links = {
        link.url: link
        for link in parse_sql_main_index(out_dir)
        if link.url not in all_links
    }
    if orphaned_sql_links:
        all_links.update(orphaned_sql_links)
        print('    {lightyellow}√ Added {} orphaned links from existing SQL index...{reset}'.format(len(orphaned_sql_links), **ANSI))

    # Links in data dir indexes but not in main index
    orphaned_data_dir_links = {
        link.url: link
        for link in parse_json_links_details(out_dir)
        if link.url not in all_links
    }
    if orphaned_data_dir_links:
        all_links.update(orphaned_data_dir_links)
        print('    {lightyellow}√ Added {} orphaned links from existing archive directories.{reset}'.format(len(orphaned_data_dir_links), **ANSI))

    # Links in invalid/duplicate data dirs
    invalid_folders = {
        folder: link
        for folder, link in get_invalid_folders(all_links.values(), out_dir=out_dir).items()
    }
    if invalid_folders:
        print('    {lightyellow}! Skipped adding {} invalid link data directories.{reset}'.format(len(invalid_folders), **ANSI))
        print('        X ' + '\n        X '.join(f'{folder} {link}' for folder, link in invalid_folders.items()))
        print()
        print('    {lightred}Hint:{reset} For more information about the link data directories that were skipped, run:'.format(**ANSI))
        print('        archivebox info')
        print('        archivebox list --status=invalid')


    write_main_index(list(all_links.values()), out_dir=out_dir)

    print('\n{green}------------------------------------------------------------------{reset}'.format(**ANSI))
    if existing_index:
        print('{green}[√] Done. Verified and updated the existing ArchiveBox collection.{reset}'.format(**ANSI))
    else:
        print('{green}[√] Done. A new ArchiveBox collection was initialized ({} links).{reset}'.format(len(all_links), **ANSI))
    print()
    print('    To view your archive index, open:')
    print('        {}'.format(os.path.join(out_dir, HTML_INDEX_FILENAME)))
    print()
    print('    To add new links, you can run:')
    print("        archivebox add 'https://example.com'")
    print()
    print('    For more usage and examples, run:')
    print('        archivebox help')


def info(out_dir: str=OUTPUT_DIR) -> None:
    check_data_folder(out_dir=out_dir)

    print('{green}[*] Scanning archive collection main index...{reset}'.format(**ANSI))
    print(f'    {out_dir}/*')
    num_bytes, num_dirs, num_files = get_dir_size(out_dir, recursive=False, pattern='index.')
    size = human_readable_size(num_bytes)
    print(f'    Size: {size} across {num_files} files')
    print()

    links = list(load_main_index(out_dir=out_dir))
    num_json_links = len(links)
    num_sql_links = sum(1 for link in parse_sql_main_index(out_dir=out_dir))
    num_html_links = sum(1 for url in parse_html_main_index(out_dir=out_dir))
    num_link_details = sum(1 for link in parse_json_links_details(out_dir=out_dir))
    users = get_admins().values_list('username', flat=True)
    print(f'    > JSON Main Index: {num_json_links} links'.ljust(36),  f'(found in {JSON_INDEX_FILENAME})')
    print(f'    > SQL Main Index: {num_sql_links} links'.ljust(36), f'(found in {SQL_INDEX_FILENAME})')
    print(f'    > HTML Main Index: {num_html_links} links'.ljust(36), f'(found in {HTML_INDEX_FILENAME})')
    print(f'    > JSON Link Details: {num_link_details} links'.ljust(36), f'(found in {ARCHIVE_DIR_NAME}/*/index.json)')

    print(f'    > Admin: {len(users)} users {", ".join(users)}'.ljust(36), f'(found in {SQL_INDEX_FILENAME})')
    
    if num_html_links != len(links) or num_sql_links != len(links):
        print()
        print('    {lightred}Hint:{reset} You can fix index count differences automatically by running:'.format(**ANSI))
        print('        archivebox init')
    
    if not users:
        print()
        print('    {lightred}Hint:{reset} You can create an admin user by running:'.format(**ANSI))
        print('        archivebox manage createsuperuser')

    print()
    print('{green}[*] Scanning archive collection link data directories...{reset}'.format(**ANSI))
    print(f'    {ARCHIVE_DIR}/*')

    num_bytes, num_dirs, num_files = get_dir_size(ARCHIVE_DIR)
    size = human_readable_size(num_bytes)
    print(f'    Size: {size} across {num_files} files in {num_dirs} directories')
    print()

    num_indexed = len(get_indexed_folders(links, out_dir=out_dir))
    num_archived = len(get_archived_folders(links, out_dir=out_dir))
    num_unarchived = len(get_unarchived_folders(links, out_dir=out_dir))
    print(f'    > indexed: {num_indexed}'.ljust(36), f'({get_indexed_folders.__doc__})')
    print(f'      > archived: {num_archived}'.ljust(36), f'({get_archived_folders.__doc__})')
    print(f'      > unarchived: {num_unarchived}'.ljust(36), f'({get_unarchived_folders.__doc__})')
    
    num_present = len(get_present_folders(links, out_dir=out_dir))
    num_valid = len(get_valid_folders(links, out_dir=out_dir))
    print()
    print(f'    > present: {num_present}'.ljust(36), f'({get_present_folders.__doc__})')
    print(f'      > valid: {num_valid}'.ljust(36), f'({get_valid_folders.__doc__})')
    
    duplicate = get_duplicate_folders(links, out_dir=out_dir)
    orphaned = get_orphaned_folders(links, out_dir=out_dir)
    corrupted = get_corrupted_folders(links, out_dir=out_dir)
    unrecognized = get_unrecognized_folders(links, out_dir=out_dir)
    num_invalid = len({**duplicate, **orphaned, **corrupted, **unrecognized})
    print(f'      > invalid: {num_invalid}'.ljust(36), f'({get_invalid_folders.__doc__})')
    print(f'        > duplicate: {len(duplicate)}'.ljust(36), f'({get_duplicate_folders.__doc__})')
    print(f'        > orphaned: {len(orphaned)}'.ljust(36), f'({get_orphaned_folders.__doc__})')
    print(f'        > corrupted: {len(corrupted)}'.ljust(36), f'({get_corrupted_folders.__doc__})')
    print(f'        > unrecognized: {len(unrecognized)}'.ljust(36), f'({get_unrecognized_folders.__doc__})')
    
    if num_indexed:
        print()
        print('    {lightred}Hint:{reset} You can list link data directories by status like so:'.format(**ANSI))
        print('        archivebox list --status=<status>  (e.g. indexed, corrupted, archived, etc.)')

    if orphaned:
        print()
        print('    {lightred}Hint:{reset} To automatically import orphaned data directories into the main index, run:'.format(**ANSI))
        print('        archivebox init')

    if num_invalid:
        print()
        print('    {lightred}Hint:{reset} You may need to manually remove or fix some invalid data directories, afterwards make sure to run:'.format(**ANSI))
        print('        archivebox init')
    
    print()


@enforce_types
def add(import_str: Optional[str]=None,
        import_path: Optional[str]=None,
        update_all: bool=not ONLY_NEW,
        index_only: bool=False,
        out_dir: str=OUTPUT_DIR) -> List[Link]:
    """The main ArchiveBox entrancepoint. Everything starts here."""

    check_data_folder(out_dir=out_dir)

    if import_str and import_path:
        stderr(
            '[X] You should pass either an import path as an argument, '
            'or pass a list of links via stdin, but not both.\n',
            color='red',
        )
        raise SystemExit(2)
    elif import_str:
        import_path = save_stdin_to_sources(import_str, out_dir=out_dir)
    else:
        import_path = save_file_to_sources(import_path, out_dir=out_dir)

    check_dependencies()

    # Step 1: Load list of links from the existing index
    #         merge in and dedupe new links from import_path
    all_links: List[Link] = []
    new_links: List[Link] = []
    all_links = load_main_index(out_dir=out_dir)
    if import_path:
        all_links, new_links = import_new_links(all_links, import_path, out_dir=out_dir)

    # Step 2: Write updated index with deduped old and new links back to disk
    write_main_index(links=all_links, out_dir=out_dir)

    if index_only:
        return all_links
        
    # Step 3: Run the archive methods for each link
    links = all_links if update_all else new_links
    log_archiving_started(len(links))
    idx: int = 0
    link: Link = None                                             # type: ignore
    try:
        for idx, link in enumerate(links):
            archive_link(link, out_dir=link.link_dir)

    except KeyboardInterrupt:
        log_archiving_paused(len(links), idx, link.timestamp if link else '0')
        raise SystemExit(0)

    except:
        print()
        raise    

    log_archiving_finished(len(links))

    # Step 4: Re-write links index with updated titles, icons, and resources
    all_links = load_main_index(out_dir=out_dir)
    write_main_index(links=list(all_links), out_dir=out_dir, finished=True)
    return all_links

@enforce_types
def remove(filter_str: Optional[str]=None,
           filter_patterns: Optional[List[str]]=None,
           filter_type: str='exact',
           after: Optional[float]=None,
           before: Optional[float]=None,
           yes: bool=False,
           delete: bool=False,
           out_dir: str=OUTPUT_DIR) -> List[Link]:
    
    check_data_folder(out_dir=out_dir)

    if filter_str and filter_patterns:
        stderr(
            '[X] You should pass either a pattern as an argument, '
            'or pass a list of patterns via stdin, but not both.\n',
            color='red',
        )
        raise SystemExit(2)
    elif not (filter_str or filter_patterns):
        stderr(
            '[X] You should pass either a pattern as an argument, '
            'or pass a list of patterns via stdin.',
            color='red',
        )
        stderr()
        stderr('    {lightred}Hint:{reset} To remove all urls you can run:'.format(**ANSI))
        stderr("        archivebox remove --filter-type=regex '.*'")
        stderr()
        raise SystemExit(2)
    elif filter_str:
        filter_patterns = [ptn.strip() for ptn in filter_str.split('\n')]

    log_list_started(filter_patterns, filter_type)
    timer = TimedProgress(360, prefix='      ')
    try:
        links = list(list_links(
            filter_patterns=filter_patterns,
            filter_type=filter_type,
            after=after,
            before=before,
        ))
    finally:
        timer.end()

    if not len(links):
        log_removal_finished(0, 0)
        raise SystemExit(1)


    log_list_finished(links)
    log_removal_started(links, yes=yes, delete=delete)

    timer = TimedProgress(360, prefix='      ')
    try:
        to_keep = []
        all_links = load_main_index(out_dir=out_dir)
        for link in all_links:
            should_remove = (
                (after is not None and float(link.timestamp) < after)
                or (before is not None and float(link.timestamp) > before)
                or link_matches_filter(link, filter_patterns, filter_type)
            )
            if not should_remove:
                to_keep.append(link)
            elif should_remove and delete:
                shutil.rmtree(link.link_dir, ignore_errors=True)
    finally:
        timer.end()

    write_main_index(links=to_keep, out_dir=out_dir, finished=True)
    log_removal_finished(len(all_links), len(to_keep))
    
    return to_keep

@enforce_types
def update(resume: Optional[float]=None,
           only_new: bool=not ONLY_NEW,
           index_only: bool=False,
           overwrite: bool=False,
           filter_patterns_str: Optional[str]=None,
           filter_patterns: Optional[List[str]]=None,
           filter_type: Optional[str]=None,
           status: Optional[str]=None,
           after: Optional[str]=None,
           before: Optional[str]=None,
           out_dir: str=OUTPUT_DIR) -> List[Link]:
    """The main ArchiveBox entrancepoint. Everything starts here."""

    check_dependencies()
    check_data_folder(out_dir=out_dir)

    # Step 1: Load list of links from the existing index
    #         merge in and dedupe new links from import_path
    all_links: List[Link] = []
    new_links: List[Link] = []
    all_links = load_main_index(out_dir=out_dir)

    # Step 2: Write updated index with deduped old and new links back to disk
    write_main_index(links=list(all_links), out_dir=out_dir)

    # Step 3: Filter for selected_links
    matching_links = list_links(
        filter_patterns=filter_patterns,
        filter_type=filter_type,
        before=before,
        after=after,
    )
    matching_folders = list_folders(
        links=list(matching_links),
        status=status,
        out_dir=out_dir,
    )
    all_links = [link for link in matching_folders.values() if link]

    if index_only:
        return all_links
        
    # Step 3: Run the archive methods for each link
    links = new_links if only_new else all_links
    log_archiving_started(len(links), resume)
    idx: int = 0
    link: Link = None                                             # type: ignore
    try:
        for idx, link in enumerate(links_after_timestamp(links, resume)):
            archive_link(link, overwrite=overwrite, out_dir=link.link_dir)

    except KeyboardInterrupt:
        log_archiving_paused(len(links), idx, link.timestamp if link else '0')
        raise SystemExit(0)

    except:
        print()
        raise    

    log_archiving_finished(len(links))

    # Step 4: Re-write links index with updated titles, icons, and resources
    all_links = load_main_index(out_dir=out_dir)
    write_main_index(links=list(all_links), out_dir=out_dir, finished=True)
    return all_links

@enforce_types
def list_all(filter_patterns_str: Optional[str]=None,
             filter_patterns: Optional[List[str]]=None,
             filter_type: str='exact',
             status: Optional[str]=None,
             after: Optional[float]=None,
             before: Optional[float]=None,
             sort: Optional[str]=None,
             csv: Optional[str]=None,
             json: Optional[str]=None,
             out_dir: str=OUTPUT_DIR) -> Iterable[Link]:
    
    check_data_folder(out_dir=out_dir)

    if filter_patterns and filter_patterns_str:
        stderr(
            '[X] You should either pass filter patterns as an arguments '
            'or via stdin, but not both.\n',
            color='red',
        )
        raise SystemExit(2)
    elif filter_patterns_str:
        filter_patterns = filter_patterns_str.split('\n')


    links = list_links(
        filter_patterns=filter_patterns,
        filter_type=filter_type,
        before=before,
        after=after,
    )

    if sort:
        links = sorted(links, key=lambda link: getattr(link, sort))

    folders = list_folders(
        links=list(links),
        status=status,
        out_dir=out_dir,
    )
    
    if csv:
        print(links_to_csv(folders.values(), csv_cols=csv.split(','), header=True))
    elif json:
        print(to_json(folders.values(), indent=4, sort_keys=True))
    else:
        print(folders_to_str(folders))
    raise SystemExit(not folders)


@enforce_types
def list_links(filter_patterns: Optional[List[str]]=None,
               filter_type: str='exact',
               after: Optional[float]=None,
               before: Optional[float]=None,
               out_dir: str=OUTPUT_DIR) -> Iterable[Link]:
    
    check_data_folder(out_dir=out_dir)

    all_links = load_main_index(out_dir=out_dir)

    for link in all_links:
        if after is not None and float(link.timestamp) < after:
            continue
        if before is not None and float(link.timestamp) > before:
            continue
        
        if filter_patterns:
            if link_matches_filter(link, filter_patterns, filter_type):
                yield link
        else:
            yield link

@enforce_types
def list_folders(links: List[Link],
                 status: str,
                 out_dir: str=OUTPUT_DIR) -> Dict[str, Optional[Link]]:
    
    check_data_folder()

    if status == 'indexed':
        return get_indexed_folders(links, out_dir=out_dir)
    elif status == 'archived':
        return get_archived_folders(links, out_dir=out_dir)
    elif status == 'unarchived':
        return get_unarchived_folders(links, out_dir=out_dir)

    elif status == 'present':
        return get_present_folders(links, out_dir=out_dir)
    elif status == 'valid':
        return get_valid_folders(links, out_dir=out_dir)
    elif status == 'invalid':
        return get_invalid_folders(links, out_dir=out_dir)

    elif status == 'duplicate':
        return get_duplicate_folders(links, out_dir=out_dir)
    elif status == 'orphaned':
        return get_orphaned_folders(links, out_dir=out_dir)
    elif status == 'corrupted':
        return get_corrupted_folders(links, out_dir=out_dir)
    elif status == 'unrecognized':
        return get_unrecognized_folders(links, out_dir=out_dir)

    raise ValueError('Status not recognized.')


def config(config_options_str: Optional[str]=None,
           config_options: Optional[List[str]]=None,
           get: bool=False,
           set: bool=False,
           reset: bool=False,
           out_dir: str=OUTPUT_DIR) -> None:

    check_data_folder(out_dir=out_dir)

    if config_options and config_options_str:
        stderr(
            '[X] You should either pass config values as an arguments '
            'or via stdin, but not both.\n',
            color='red',
        )
        raise SystemExit(2)
    elif config_options_str:
        config_options = stdin_raw_text.split('\n')

    config_options = config_options or []

    no_args = not (get or set or reset or config_options)

    matching_config: ConfigDict = {}
    if get or no_args:
        if config_options:
            config_options = [get_real_name(key) for key in config_options]
            matching_config = {key: CONFIG[key] for key in config_options if key in CONFIG}
            failed_config = [key for key in config_options if key not in CONFIG]
            if failed_config:
                stderr()
                stderr('[X] These options failed to get', color='red')
                stderr('    {}'.format('\n    '.join(config_options)))
                raise SystemExit(1)
        else:
            matching_config = CONFIG
        
        print(printable_config(matching_config))
        raise SystemExit(not matching_config)
    elif set:
        new_config = {}
        failed_options = []
        for line in config_options:
            if line.startswith('#') or not line.strip():
                continue
            if '=' not in line:
                stderr('[X] Config KEY=VALUE must have an = sign in it', color='red')
                stderr(f'    {line}')
                raise SystemExit(2)

            raw_key, val = line.split('=')
            raw_key = raw_key.upper().strip()
            key = get_real_name(raw_key)
            if key != raw_key:
                stderr(f'[i] Note: The config option {raw_key} has been renamed to {key}, please use the new name going forwards.', color='lightyellow')

            if key in CONFIG:
                new_config[key] = val.strip()
            else:
                failed_options.append(line)

        if new_config:
            before = CONFIG
            matching_config = write_config_file(new_config, out_dir=OUTPUT_DIR)
            after = load_all_config()
            print(printable_config(matching_config))

            side_effect_changes: ConfigDict = {}
            for key, val in after.items():
                if key in USER_CONFIG and (before[key] != after[key]) and (key not in matching_config):
                    side_effect_changes[key] = after[key]

            if side_effect_changes:
                stderr()
                stderr('[i] Note: This change also affected these other options that depended on it:', color='lightyellow')
                print('    {}'.format(printable_config(side_effect_changes, prefix='    ')))
        if failed_options:
            stderr()
            stderr('[X] These options failed to set:', color='red')
            stderr('    {}'.format('\n    '.join(failed_options)))
        raise SystemExit(bool(failed_options))
    elif reset:
        stderr('[X] This command is not implemented yet.', color='red')
        stderr('    Please manually remove the relevant lines from your config file:')
        stderr(f'        {CONFIG_FILE}')
        raise SystemExit(2)

    else:
        stderr('[X] You must pass either --get or --set, or no arguments to get the whole config.', color='red')
        stderr('    archivebox config')
        stderr('    archivebox config --get SOME_KEY')
        stderr('    archivebox config --set SOME_KEY=SOME_VALUE')
        raise SystemExit(2)


CRON_COMMENT = 'archivebox_schedule'

@enforce_types
def schedule(add: bool=False,
             show: bool=False,
             clear: bool=False,
             foreground: bool=False,
             run_all: bool=False,
             quiet: bool=False,
             every: Optional[str]=None,
             import_path: Optional[str]=None,
             out_dir: str=OUTPUT_DIR):
    
    check_data_folder(out_dir=out_dir)

    os.makedirs(os.path.join(out_dir, LOGS_DIR_NAME), exist_ok=True)

    cron = CronTab(user=True)
    cron = dedupe_jobs(cron)

    existing_jobs = list(cron.find_comment(CRON_COMMENT))
    if foreground or run_all:
        if import_path or (not existing_jobs):
            stderr('{red}[X] You must schedule some jobs first before running in foreground mode.{reset}'.format(**ANSI))
            stderr('    archivebox schedule --every=hour https://example.com/some/rss/feed.xml')
            raise SystemExit(1)
        print('{green}[*] Running {} ArchiveBox jobs in foreground task scheduler...{reset}'.format(len(existing_jobs), **ANSI))
        if run_all:
            try:
                for job in existing_jobs:
                    sys.stdout.write(f'  > {job.command}')
                    sys.stdout.flush()
                    job.run()
                    sys.stdout.write(f'\r  √ {job.command}\n')
            except KeyboardInterrupt:
                print('\n{green}[√] Stopped.{reset}'.format(**ANSI))
                raise SystemExit(1)
        if foreground:
            try:
                for result in cron.run_scheduler():
                    print(result)
            except KeyboardInterrupt:
                print('\n{green}[√] Stopped.{reset}'.format(**ANSI))
                raise SystemExit(1)

    elif show:
        if existing_jobs:
            print('\n'.join(str(cmd) for cmd in existing_jobs))
        else:
            stderr('{red}[X] There are no ArchiveBox cron jobs scheduled for your user ({}).{reset}'.format(USER, **ANSI))
            stderr('    To schedule a new job, run:')
            stderr('        archivebox schedule --every=[timeperiod] https://example.com/some/rss/feed.xml')
        raise SystemExit(0)

    elif clear:
        print(cron.remove_all(comment=CRON_COMMENT))
        cron.write()
        raise SystemExit(0)

    elif every:
        quoted = lambda s: f'"{s}"' if s and ' ' in s else s
        cmd = [
            'cd',
            quoted(out_dir),
            '&&',
            quoted(ARCHIVEBOX_BINARY),
            *(['add', f'"{import_path}"'] if import_path else ['update']),
            '2>&1',
            '>',
            quoted(os.path.join(LOGS_DIR, 'archivebox.log')),

        ]
        new_job = cron.new(command=' '.join(cmd), comment=CRON_COMMENT)

        if every in ('minute', 'hour', 'day', 'week', 'month', 'year'):
            set_every = getattr(new_job.every(), every)
            set_every()
        elif CronSlices.is_valid(every):
            new_job.setall(every)
        else:
            stderr('{red}[X] Got invalid timeperiod for cron task.{reset}'.format(**ANSI))
            stderr('    It must be one of minute/hour/day/week/month')
            stderr('    or a quoted cron-format schedule like:')
            stderr('        archivebox init --every=day https://example.com/some/rss/feed.xml')
            stderr('        archivebox init --every="0/5 * * * *" https://example.com/some/rss/feed.xml')
            raise SystemExit(1)

        cron = dedupe_jobs(cron)
        cron.write()

        total_runs = sum(j.frequency_per_year() for j in cron)
        existing_jobs = list(cron.find_comment(CRON_COMMENT))

        print()
        print('{green}[√] Scheduled new ArchiveBox cron job for user: {} ({} jobs are active).{reset}'.format(USER, len(existing_jobs), **ANSI))
        print('\n'.join(f'  > {cmd}' if str(cmd) == str(new_job) else f'    {cmd}' for cmd in existing_jobs))
        if total_runs > 60 and not quiet:
            stderr()
            stderr('{lightyellow}[!] With the current cron config, ArchiveBox is estimated to run >{} times per year.{reset}'.format(total_runs, **ANSI))
            stderr(f'    Congrats on being an enthusiastic internet archiver! 👌')
            stderr()
            stderr('    Make sure you have enough storage space available to hold all the data.')
            stderr('    Using a compressed/deduped filesystem like ZFS is recommended if you plan on archiving a lot.')
        raise SystemExit(0)





def server(runserver_args: Optional[List[str]]=None, reload: bool=False, out_dir: str=OUTPUT_DIR) -> None:
    runserver_args = runserver_args or []
    check_data_folder(out_dir=out_dir)

    setup_django(out_dir)
    from django.core.management import call_command
    from django.contrib.auth.models import User

    if IS_TTY and not User.objects.filter(is_superuser=True).exists():
        print('{lightyellow}[!] No admin users exist yet, you will not be able to edit links in the UI.{reset}'.format(**ANSI))
        print()
        print('    To create an admin user, run:')
        print('        archivebox manage createsuperuser')
        print()

    print('{green}[+] Starting ArchiveBox webserver...{reset}'.format(**ANSI))
    if not reload:
        runserver_args.append('--noreload')

    call_command("runserver", *runserver_args)


def manage(args: Optional[List[str]]=None, out_dir: str=OUTPUT_DIR) -> None:
    check_data_folder(out_dir=out_dir)

    setup_django(out_dir)
    from django.core.management import execute_from_command_line

    execute_from_command_line([f'{ARCHIVEBOX_BINARY} manage', *(args or ['help'])])

def shell(out_dir: str=OUTPUT_DIR) -> None:
    check_data_folder(out_dir=out_dir)

    setup_django(OUTPUT_DIR)
    from django.core.management import call_command
    call_command("shell_plus")

# Helpers

def printable_config(config: ConfigDict, prefix: str='') -> str:
    return f'\n{prefix}'.join(
        f'{key}={val}'
        for key, val in config.items()
        if not (isinstance(val, dict) or callable(val))
    )

def dedupe_jobs(cron: CronTab) -> CronTab:
    deduped: Set[Tuple[str, str]] = set()

    for job in list(cron):
        unique_tuple = (str(job.slices), job.command)
        if unique_tuple not in deduped:
            deduped.add(unique_tuple)
        cron.remove(job)

    for schedule, command in deduped:
        job = cron.new(command=command, comment=CRON_COMMENT)
        job.setall(schedule)
        job.enable()

    return cron


def print_folder_status(name, folder):
    if folder['enabled']:
        if folder['is_valid']:
            color, symbol, note = 'green', '√', 'valid'
        else:
            color, symbol, note, num_files = 'red', 'X', 'invalid', '?'
    else:
        color, symbol, note, num_files = 'lightyellow', '-', 'disabled', '-'

    if folder['path']:
        if os.path.exists(folder['path']):
            num_files = (
                f'{len(os.listdir(folder["path"]))} files'
                if os.path.isdir(folder['path']) else
                human_readable_size(os.path.getsize(folder['path']))
            )
        else:
            num_files = 'missing'

        if ' ' in folder['path']:
            folder['path'] = f'"{folder["path"]}"'

    print(
        ANSI[color],
        symbol,
        ANSI['reset'],
        name.ljust(22),
        (folder["path"] or '').ljust(76),
        num_files.ljust(14),
        ANSI[color],
        note,
        ANSI['reset'],
    )


def print_dependency_version(name, dependency):
    if dependency['enabled']:
        if dependency['is_valid']:
            color, symbol, note = 'green', '√', 'valid'
            version = 'v' + re.search(r'[\d\.]+', dependency['version'])[0]
        else:
            color, symbol, note, version = 'red', 'X', 'invalid', '?'
    else:
        color, symbol, note, version = 'lightyellow', '-', 'disabled', '-'

    if ' ' in dependency["path"]:
        dependency["path"] = f'"{dependency["path"]}"'

    print(
        ANSI[color],
        symbol,
        ANSI['reset'],
        name.ljust(22),
        (dependency["path"] or '').ljust(76),
        version.ljust(14),
        ANSI[color],
        note,
        ANSI['reset'],
    )
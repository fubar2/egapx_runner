#!/usr/bin/env python
# requires pip install pyyaml
#
import shlex
import shutil
import sys
import os
import argparse
import subprocess
import tempfile
import re
import time
import datetime
from collections import defaultdict
from ftplib import FTP
from ftplib import FTP_TLS
import ftplib
from pathlib import Path
from typing import List
from urllib.request import urlopen
import json
import sqlite3
import stat

import yaml

VERBOSITY_DEFAULT=0
VERBOSITY_QUIET=-1
VERBOSITY_VERBOSE=1

FTP_EGAP_PROTOCOL = "https"
FTP_EGAP_SERVER = "ftp.ncbi.nlm.nih.gov"
FTP_EGAP_ROOT_PATH = "genomes/TOOLS/EGAP/support_data"
FTP_EGAP_ROOT = f"{FTP_EGAP_PROTOCOL}://{FTP_EGAP_SERVER}/{FTP_EGAP_ROOT_PATH}"
DATA_VERSION = "current"
dataset_taxonomy_url = "https://api.ncbi.nlm.nih.gov/datasets/v2alpha/taxonomy/taxon/"

user_cache_dir = ''

def parse_args(argv):
    parser = argparse.ArgumentParser(description="Main script for EGAPx")
    group = parser.add_argument_group('run')
    group.add_argument("filename", nargs='?', help="YAML file with input: section with at least genome: and reads: parameters")
    group.add_argument("-o", "--output", help="Output path", default="")
    parser.add_argument("-e", "--executor", help="Nextflow executor, one of docker, singularity, aws, or local (for NCBI internal use only). Uses corresponding Nextflow config file", default="local")
    parser.add_argument("-c", "--config-dir", help="Directory for executor config files, default is ./egapx_config. Can be also set as env EGAPX_CONFIG_DIR", default="")
    parser.add_argument("-w", "--workdir", help="Working directory for cloud executor", default="")
    parser.add_argument("-r", "--report", help="Report file prefix for report (.report.html) and timeline (.timeline.html) files, default is in output directory", default="")
    parser.add_argument("-n", "--dry-run", action="store_true", default=False)
    parser.add_argument("-st", "--stub-run", action="store_true", default=False)
    parser.add_argument("-so", "--summary-only", help="Print result statistics only if available, do not compute result", action="store_true", default=False)
    group = parser.add_argument_group('download')
    group.add_argument("-dl", "--download-only", help="Download external files to local storage, so that future runs can be isolated", action="store_true", default=False)
    parser.add_argument("-lc", "--local-cache", help="Where to store the downloaded files", default="")
    parser.add_argument("-q", "--quiet", dest='verbosity', action='store_const', const=VERBOSITY_QUIET, default=VERBOSITY_DEFAULT)
    parser.add_argument("-v", "--verbose", dest='verbosity', action='store_const', const=VERBOSITY_VERBOSE, default=VERBOSITY_DEFAULT)
    parser.add_argument("-fn", "--func_name", help="func_name", default="")
    return parser.parse_args(argv[1:])


class FtpDownloader:
    def __init__(self):
        self.ftp = None

    def connect(self, host):
        self.host = host
        self.reconnect() 

    def reconnect(self):
        self.ftp = FTP(self.host)
        self.ftp.login()
        self.ftp.set_debuglevel(0)
       
    ##ftp_types = set()
    def download_ftp_file(self, ftp_name, local_path):
        # print(f"file: {ftp_name}")
        # print(f"f: { os.path.dirname(local_path)}")

        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        try:
            with open(local_path, 'wb') as f:
                self.ftp.retrbinary("RETR {0}".format(ftp_name), f.write)
            # print("downloaded: {0}".format(local_path))
            return True
        except FileNotFoundError:
            print("FAILED FNF: {0}".format(local_path))
        except EOFError:
            print("FAILED EOF: {0}".format(local_path))
            time.sleep(1)
            self.reconnect()
            print("retrying...")
            r = self.download_ftp_file(ftp_name, local_path)
            print("downloaded on retry: {0}".format(local_path))
            return r
        except BrokenPipeError:
            print("FAILED BP: {0}".format(local_path))
        except IsADirectoryError:
            ## same as 550 but pre-exists
            ## os.remove(local_path)
            return 550
        except ftplib.error_perm:
            ## ftplib.error_perm: 550 genomes/TOOLS/EGAP/ortholog_references/9606/current: Not a regular file
            ## its a symlink to a dir.
            os.remove(local_path)
            return 550
        return False

    # item: ('Eublepharis_macularius', {'modify': '20240125225739', 'perm': 'fle', 'size': '4096', 'type': 'dir', 'unique': '6CU599079F', 'unix.group': '562', 'unix.mode': '0444', 'unix.owner': '14'}
    def should_download_file(self, ftp_item, local_name):
        metadata = ftp_item[1]
        ftp_modify = datetime.datetime.strptime(metadata['modify'], '%Y%m%d%H%M%S')
        ftp_size = int(metadata['size'])
        ftp_type = metadata['type']

        local_stat = []
        try:
            local_stat = os.stat(local_name)
        except FileNotFoundError:
            return True
        except NotADirectoryError:
            return True
        local_is_dir = stat.S_ISDIR(local_stat.st_mode)
        #print(f"item: {ftp_item}")
        #print(f"stat: {local_stat}") 
 
        local_stat_dt = datetime.datetime.fromtimestamp(local_stat.st_mtime)

        #print(f"should_dl: {ftp_size != local_stat.st_size}  {ftp_modify > local_stat_dt}  ")

        if (ftp_type == 'file' and ftp_size != local_stat.st_size) or (ftp_type=='OS.unix=symlink' and ftp_size >= local_stat.st_size):
            return True

        if ftp_modify > local_stat_dt:
            return True

        return False

    ## types
    ## cdir and pdir: . and ..: do nothing
    ## file: a file
    ## dir: a dir
    ## OS.unix=symlink: symlink to a file, treat like a file
    def download_ftp_dir(self, ftp_path, local_path):
        """ replicates a directory on an ftp server recursively """
        for ftp_item in self.ftp.mlsd(ftp_path):
            # print(f"ftp_item: {ftp_item}")
            ##print(f"  name: {ftp_item[0]}")
            ##print(f"  type: {ftp_item[1]['type']}")
            name = ftp_item[0]
            next_ftp_name="/".join([ftp_path,name])
            next_local_name=os.sep.join([local_path,name])
            ftp_type = ftp_item[1]['type']
            ##ftp_types.add(ftp_type)
            if ftp_type=='dir':
                self.download_ftp_dir(next_ftp_name, next_local_name)
            elif ftp_type=='file' or ftp_type=='OS.unix=symlink':
                if self.should_download_file(ftp_item, next_local_name):
                    r = self.download_ftp_file(next_ftp_name, next_local_name)
                    if r == 550:
                        self.download_ftp_dir(next_ftp_name, next_local_name)
                    ##time.sleep(0.1)
            ## else: . or .. do nothing


def download_egapx_ftp_data(local_cache_dir):
    global user_cache_dir
    manifest_url = f"{FTP_EGAP_ROOT}/{DATA_VERSION}.mft"
    manifest = urlopen(manifest_url)
    manifest_path = f"{user_cache_dir}/{DATA_VERSION}.mft"
    manifest_list = []
    for line in manifest:
        line = line.decode("utf-8").strip()
        if not line or line[0] == '#':
            continue
        manifest_list.append(line)
        print(f"Downloading {line}")
        ftpd = FtpDownloader()
        ftpd.connect(FTP_EGAP_SERVER)
        ftpd.download_ftp_dir(FTP_EGAP_ROOT_PATH+f"/{line}", f"{local_cache_dir}/{line}")
    if user_cache_dir:
        with open(manifest_path, 'wt') as f:
            for line in manifest_list:
                f.write(f"{line}\n")
    return 0


def repackage_inputs(run_inputs):
    "Repackage input parameters into 'input' key if not already there"
    if 'input' in run_inputs:
        return run_inputs
    new_inputs = {}
    new_inputs['input'] = {}
    for key in run_inputs:
        if key not in { 'tasks', 'output' }:
            new_inputs['input'][key] = run_inputs[key]
        else:
            new_inputs[key] = run_inputs[key]
    return new_inputs


def convert_value(value):
    "Convert paths to absolute paths when possible, look into objects and lists"
    if isinstance(value, dict):
        return {k: convert_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [convert_value(v) for v in value]
    else:
        if value == '' or re.match(r'[a-z0-9]{2,5}://', value) or not os.path.exists(value):
            # do not convert - this is a URL or empty string or non-file
            return value
        # convert to absolute path
        return str(Path(value).absolute())


path_inputs = { 'genome', 'hmm', 'softmask', 'reads_metadata', 'organelles', 'proteins', 'reads', 'rnaseq_alignments', 'protein_alignments' }
def convert_paths(run_inputs):
    "Convert paths to absolute paths where appropriate"
    input_root = run_inputs['input']
    for key in input_root:
        if key in path_inputs:
            if isinstance(input_root[key], list):
                input_root[key] = [convert_value(v) for v in input_root[key]]
            else:
                input_root[key] = convert_value(input_root[key])
    if 'output' in run_inputs:
        run_inputs['output'] = convert_value(run_inputs['output'])


def prepare_reads(run_inputs):
    """Reformat reads input to be in 'fromPairs' format expected by egapx, i.e. [sample_id, [read1, read2]]
    Generate reads metadata file with minimal information - paired/unpaired and valid for existing libraries"""
    if 'reads' not in run_inputs['input'] or 'output' not in run_inputs:
        return
    prefixes = defaultdict(list)
    reads = run_inputs['input']['reads']
    if type(reads) == str:
        del run_inputs['input']['reads']
        run_inputs['input']['reads_query'] = reads
        return
    # Create fake metadata file for reads containing only one meaningful information piece - pairedness
    # Have an entry in this file only for SRA runs - files with prefixes SRR, ERR, and DRR and digits
    # Non-SRA reads are handled by rnaseq_collapse internally
    has_files = False
    for rf in run_inputs['input']['reads']:
        if type(rf) == str:
            name = Path(rf).parts[-1]
            mo = re.match(r'([^._]+)', name)
            if mo and mo.group(1) != name:
                has_files = True
                prefixes[mo.group(1)].append(rf)
        elif type(rf) == list:
            has_files = True
            names = list(map(lambda x: re.match(r'([^.]+)', Path(x).parts[-1]).group(1), rf))
            # Find common prefix
            names.sort()
            if len(names) == 1:
                sample_id = names[0]
            else:
                for i in range(min(len(names[0]), len(names[-1]))):
                    if names[0][i] != names[-1][i]:
                        break
                sample_id = names[0][0:i]
            # Dont end prefix with . or _
            i = len(sample_id) - 1
            while i > 0 and sample_id[-1] in "._":
                sample_id = sample_id[:-1]
                i -= 1
            prefixes[sample_id] = rf
    if has_files: # len(prefixes):
        # Always create metadata file even if it's empty
        output = run_inputs['output']
        with tempfile.NamedTemporaryFile(mode='w', delete=False, dir=output, prefix='egapx_reads_metadata_', suffix='.tsv') as f:
            for k, v in prefixes.items():
                if re.fullmatch(r'([DES]RR[0-9]+)', k):
                    paired = 'paired' if len(v) == 2 else 'unpaired'
                    # SRR9005248	NA	paired	2	2	NA	NA	NA	NA	NA	NA	NA	0
                    rec = "\t".join([k, 'NA', paired, '2', '2', 'NA', 'NA', 'NA', 'NA', 'NA', 'NA', 'NA', '0'])
                    f.write(rec + '\n')
            f.flush()
            run_inputs['input']['reads_metadata'] = f.name
        run_inputs['input']['reads'] = [ [k, v] for k, v in prefixes.items() ]
    elif reads:
        del run_inputs['input']['reads']
        run_inputs['input']['reads_query'] = "[Accession] OR ".join(reads) + "[Accession]"


def expand_and_validate_params(run_inputs):
    """ Expand implicit parameters and validate inputs
    Args:
        run_inputs (dict): Input parameters
    Returns:
        bool: True if parameters are valid, False otherwise
    """
    inputs = run_inputs['input']

    if 'taxid' not in inputs:
        print("ERROR: Missing parameter: 'taxid'")
        return False

    taxid = inputs['taxid']

    if 'genome' not in inputs:
        print("ERROR: Missing parameter: 'genome'")
        return False

    # Check for proteins input and if empty or no input at all, add closest protein bag
    if 'proteins' not in inputs:
        proteins = get_closest_protein_bag(taxid)
        if not proteins:
            # Proteins are not specified explicitly and not found by taxid
            print(f"ERROR: Proteins are not found for tax id {inputs['taxid']}")
            print("  This organism is not supported by current NCBI protein database")
            print("  You can specify proteins manually using 'proteins' parameter")
            return False
        inputs['proteins'] = proteins

    has_rnaseq = 'reads' in inputs or 'reads_ids' in inputs or 'reads_query' in inputs
    if not has_rnaseq:
        if inputs['proteins']:
            print("WARNING: It is strongly advised to specify RNA-seq reads using 'reads' parameter\n")
        else:
            print("ERROR: Either proteins or RNA-seq reads should be provided for annotation")
            return False

    if 'hmm' not in inputs:
        best_taxid, best_hmm = get_closest_hmm(taxid)
        inputs['hmm'] = best_hmm
        inputs['hmm_taxid'] = best_taxid
    else:
        # We assume the user knows what they're doing and the training is not necessary
        inputs['hmm_taxid'] = taxid

    if 'max_intron' not in inputs:
        max_intron, genome_size_threshold = get_max_intron(taxid)
        inputs['max_intron'] = max_intron
        inputs['genome_size_threshold'] = genome_size_threshold
    else:
        # Given max_intron is a hard limit, no further calculation is necessary
        inputs['genome_size_threshold'] = 0

    return True


def manage_workdir(args):
    workdir_file = f"work_dir_{args.executor}.last"
    if args.workdir:
        os.environ['NXF_WORK'] = args.workdir
        with open(workdir_file, 'w') as f:
            f.write(args.workdir)
    else:
        if os.path.exists(workdir_file):
            with open(workdir_file) as f:
                os.environ['NXF_WORK'] = f.read().strip()
        else:
            if args.executor == 'aws':
                print("Work directory not set, use -w at least once")
                return False
    return True


def get_cache_dir():
    global user_cache_dir
    if user_cache_dir and os.path.exists(user_cache_dir):
        return user_cache_dir
    ##elif os.path.exists("/am/ftp-genomes/TOOLS/EGAP"):
    ##    return "/am/ftp-genomes/TOOLS/EGAP"
    return ""


data_version_cache = {}
def get_versioned_path(subsystem, filename):
    global data_version_cache
    global user_cache_dir
    if not data_version_cache:
        manifest_path = f"{user_cache_dir}/{DATA_VERSION}.mft"
        if user_cache_dir and os.path.exists(manifest_path):
            with open(manifest_path, 'rt') as f:
                for line in f:
                    line = line.strip()
                    if not line or line[0] == '#':
                        continue
                    parts = line.split('/')
                    if len(parts) == 2:
                        data_version_cache[parts[0]] = parts[1]
        else:
            manifest_url = f"{FTP_EGAP_ROOT}/{DATA_VERSION}.mft"
            manifest = urlopen(manifest_url)
            manifest_list = []
            for line in manifest:
                line = line.decode("utf-8").strip()
                if not line or line[0] == '#':
                    continue
                parts = line.split('/')
                if len(parts) == 2:
                    data_version_cache[parts[0]] = parts[1]
                    manifest_list.append(line)
            if user_cache_dir:
                with open(manifest_path, 'wt') as f:
                    for line in manifest_list:
                        f.write(f"{line}\n")

    if subsystem not in data_version_cache:
        return os.path.join(subsystem, filename)
    version = data_version_cache[subsystem]
    return os.path.join(subsystem, version, filename)


# Get file path for subsystem, either from cache or from remote server
def get_file_path(subsystem, filename):
    cache_dir = get_cache_dir()
    vfn = get_versioned_path(subsystem, filename)
    file_path = os.path.join(cache_dir, vfn)
    file_url = f"{FTP_EGAP_ROOT}/{vfn}"
    if os.path.exists(file_path):
        return file_path
    return file_url


def get_config(script_directory, args):
    config_file = ""
    config_dir = args.config_dir if args.config_dir else os.environ.get("EGAPX_CONFIG_DIR")
    if not config_dir:
        config_dir = Path(os.getcwd()) / "egapx_config"
    if not Path(config_dir).is_dir():
        # Create directory and copy executor config files there
        from_dir = Path(script_directory) / 'assets' / 'config' / 'executor'
        if args.verbosity >= VERBOSITY_VERBOSE:
            print(f"Copy config files from {from_dir} to {config_dir}")
        if not args.dry_run:
            os.mkdir(config_dir)
            ld = os.listdir(from_dir)
            for f in ld:
                shutil.copy2(from_dir / f, config_dir)
            print(f"Edit config files in {config_dir} to reflect your actual configuration, then repeat the command")
            return ""
    config_file = Path(config_dir) / (args.executor + '.config')
    if not config_file.is_file():
        print(f"Executor {args.executor} not supported")
        return ""
    default_configs = [ "default.config" ]
    with open(str(config_file), 'r') as f:
        config_txt = f.read().replace('\n', ' ')
        # Check whether the config sets the container
        mo = re.search(r"process.+container *=", config_txt)
        if not mo:
            default_configs.append("docker_image.config")
        # Check whether the config specifies proccess memory or CPUs
        mo = re.search(r"process.+(memory|cpus) *=", config_txt)
        if not mo:
            default_configs.append("process_resources.config")
    
    # Add mandatory configs
    config_files = [str(config_file)]
    for cf in default_configs:
        config_files.append(os.path.join(script_directory, "assets/config", cf))
    return ",".join(config_files)


lineage_cache = {}
def get_lineage(taxid):
    global lineage_cache
    if not taxid:
        return []
    if taxid in lineage_cache:
        return lineage_cache[taxid]
    # Try cached taxonomy database
    taxonomy_db_name = os.path.join(get_cache_dir(), get_versioned_path("taxonomy", "taxonomy4blast.sqlite3"))
    if os.path.exists(taxonomy_db_name):
        conn = sqlite3.connect(taxonomy_db_name)
        if conn:
            c = conn.cursor()
            lineage = [taxid]
            cur_taxid = taxid
            while cur_taxid != 1:
                c.execute("SELECT parent FROM TaxidInfo WHERE taxid = ?", (cur_taxid,))
                cur_taxid = c.fetchone()[0]
                lineage.append(cur_taxid)
            lineage.reverse()
            lineage_cache[taxid] = lineage
            return lineage
    
    # Fallback to API
    taxon_json_file = urlopen(dataset_taxonomy_url+str(taxid))
    taxon = json.load(taxon_json_file)["taxonomy_nodes"][0]
    lineage = taxon["taxonomy"]["lineage"]
    lineage.append(taxon["taxonomy"]["tax_id"])
    lineage_cache[taxid] = lineage
    return lineage


def get_tax_file(subsystem, tax_path):
    vfn = get_versioned_path(subsystem, tax_path)
    taxids_path = os.path.join(get_cache_dir(), vfn)
    taxids_url = f"{FTP_EGAP_ROOT}/{vfn}"
    taxids_file = []
    if os.path.exists(taxids_path):
        with open(taxids_path, "rb") as r:
            taxids_file = r.readlines()
    else:
        taxids_file = urlopen(taxids_url)
    return taxids_file

def get_closest_protein_bag(taxid):
    if not taxid:
        return ''

    taxids_file = get_tax_file("target_proteins", "taxid.list")
    taxids_list = []
    for line in taxids_file:
        line = line.decode("utf-8").strip()
        if len(line) == 0 or line[0] == '#':
            continue
        parts = line.split('\t')
        if len(parts) > 0:
            t = parts[0]
            taxids_list.append(int(t))

    lineage = get_lineage(taxid)

    best_taxid = None
    best_score = 0
    for t in taxids_list:
        try:
            pos = lineage.index(t)
        except ValueError:
            continue
        if pos > best_score:
            best_score = pos
            best_taxid = t

    if best_score == 0:
        return ''
    # print(best_taxid, best_score)
    return get_file_path("target_proteins", f"{best_taxid}.faa.gz")


def get_closest_hmm(taxid):
    if not taxid:
        return 0, ""

    taxids_file = get_tax_file("gnomon", "hmm_parameters/taxid.list")

    taxids_list = []
    lineages = []
    for line in taxids_file:
        parts = line.decode("utf-8").strip().split('\t')
        if len(parts) > 0:
            t = parts[0]
            taxids_list.append(t)
            if len(parts) > 1:
                l = map(lambda x: int(x) if x[-1] != ';' else int(x[:-1]), parts[1].split())
                lineages.append((int(t), list(l)+[int(t)]))

    #if len(lineages) < len(taxids_list):
    #    taxonomy_json_file = urlopen(dataset_taxonomy_url+','.join(taxids_list))
    #    taxonomy = json.load(taxonomy_json_file)["taxonomy_nodes"]
    #    lineages = [ (t["taxonomy"]["tax_id"], t["taxonomy"]["lineage"] + [t["taxonomy"]["tax_id"]]) for t in taxonomy ]

    lineage = get_lineage(taxid)

    best_lineage = None
    best_taxid = None
    best_score = 0
    for (t, l) in lineages:
        pos1 = 0
        last_match = 0
        for pos in range(len(lineage)):
            tax_id = lineage[pos]
            while tax_id != l[pos1]:
                if pos1 + 1 < len(l):
                    pos1 += 1
                else:
                    break
            if tax_id == l[pos1]:
                last_match = pos1
            else:
                break
        if last_match > best_score:
            best_score = last_match
            best_taxid = t
            best_lineage = l

    if best_score == 0:
        return 0, ""
    # print(best_lineage)
    # print(best_taxid, best_score)
    return best_taxid, get_file_path("gnomon", f"hmm_parameters/{best_taxid}.params")


PLANTS=33090
VERTEBRATES=7742
def get_max_intron(taxid):
    if not taxid:
        return 0, 0
    lineage = get_lineage(taxid)
    if PLANTS in lineage:
        return 300000, 3000000000
    elif VERTEBRATES in lineage:
        return 1200000, 2000000000
    else:
        return 600000, 500000000


def to_dict(x: List[str]):
    d = {}
    s = len(x)
    i = 0
    while i < s:
        el = x[i]
        i += 1
        if el and el[0] == '-':
            if i < s:
                v = x[i]
                if v and (v[0] != '-'  or (v[0] == '-' and ' ' in v)):
                    d[el] = v
                    i += 1
                else:
                    d[el] = ""
            else:
                d[el] = ""
        else:
            d[el] = ""
    return d

def merge_params(task_params, run_inputs):
    # Walk over the keys in run_inputs and merge them into task_params
    for k in run_inputs.keys():
        if k in task_params:
            if isinstance(task_params[k], dict) and isinstance(run_inputs[k], dict):
                task_params[k] = merge_params(task_params[k], run_inputs[k])
            else:
                task_params_dict = to_dict(shlex.split(task_params[k]))
                run_inputs_dict = to_dict(shlex.split(run_inputs[k]))
                task_params_dict.update(run_inputs_dict)
                task_params_list = []
                for k1, v in task_params_dict.items():
                    task_params_list.append(k1)
                    if v:
                        task_params_list.append(v)
                task_params[k] = shlex.join(task_params_list)
        else:
            task_params[k] = run_inputs[k]
    return task_params


def print_statistics(output):
    accept_gff = Path(output) / 'accept.gff'
    print(f"Statistics for {accept_gff}")
    counter = defaultdict(int)
    if accept_gff.exists():
        with open(accept_gff, 'rt') as f:
            for line in f:
                line = line.strip()
                if not line or line[0] == '#':
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                counter[parts[2]] += 1
    keys = list(counter.keys())
    keys.sort()
    for k in keys:
        print(f"{k:12s} {counter[k]}")


def main(argv):
    "Main script for EGAPx"
    #warn user that this is an alpha release
    print("\n!!WARNING!!\nThis is an alpha release with limited features and organism scope to collect initial feedback on execution. Outputs are not yet complete and not intended for production use.\n")

    # Parse command line
    args = parse_args(argv)

    global user_cache_dir
    if args.local_cache:
        # print(f"Local cache: {args.local_cache}")
        user_cache_dir = args.local_cache
    if args.download_only:
        if args.local_cache:
            if not args.dry_run:
                # print(f"Download only: {args.download_only}")
                os.makedirs(args.local_cache, exist_ok=True)
                download_egapx_ftp_data(args.local_cache)
            else:
                print(f"Download only to {args.local_cache}")
            return 0
        else:
            print("Local cache not set")
            return 1
    else:
        # Check that input and output set
        if not args.filename or not args.output:
            print("Input file and output directory must be set")
            return 1

    packaged_distro = bool(getattr(sys, '_MEIPASS', ''))
    script_directory = getattr(sys, '_MEIPASS', os.path.abspath(os.path.dirname(__file__)))
    config_file = get_config(script_directory, args)
    if not config_file:
        return 1
    
    # Check for workdir, set if not set, and manage last used workdir
    if not manage_workdir(args):
        return 1
   
    files_to_delete = []
    
    # Read default task parameters into a dict
    task_params = yaml.safe_load(open(Path(script_directory) / 'assets' / 'default_task_params.yaml', 'r'))
    run_inputs = repackage_inputs(yaml.safe_load(open(args.filename, 'r')))

    if not expand_and_validate_params(run_inputs):
        return 1

    # Command line overrides manifest input
    if args.output:
        run_inputs['output'] = args.output

    # Convert inputs to absolute paths
    convert_paths(run_inputs)

    # Create output directory if needed
    os.makedirs(run_inputs['output'], exist_ok=True)

    if args.summary_only:
        print_statistics(run_inputs['output'])
        return 0

    # Reformat reads into pairs in fromPairs format and add reads_metadata.tsv file
    prepare_reads(run_inputs)


    ##if True or args.download_only:
    ##    with open("./dumped_input.yaml", 'w') as f:
    ##        yaml.dump(run_inputs, f)
    ##        f.flush()
    ##return 0 

    # Add to default task parameters, if input file has some task parameters they will override the default
    task_params = merge_params(task_params, run_inputs)

    # Move output from YAML file to arguments to have more transparent Nextflow log
    output = task_params['output']
    del task_params['output']

    if args.func_name:
        task_params['func_name'] = args.func_name

    # Run nextflow process
    if args.verbosity >= VERBOSITY_VERBOSE:
        task_params['verbose'] = True
        print("Nextflow inputs:")
        print(yaml.dump(task_params))
        if 'reads_metadata' in run_inputs['input']:
            print("Reads metadata:")
            with open(run_inputs['input']['reads_metadata'], 'r') as f:
                print(f.read())
    if packaged_distro:
        main_nf = Path(script_directory) / 'nf' / 'ui.nf'
    else:
        main_nf = Path(script_directory) / '..' / 'nf' / 'ui.nf'
    nf_cmd = ["nextflow", "-C", config_file, "-log", f"{output}/nextflow.log", "run", main_nf, "--output", output]
    if args.stub_run:
        nf_cmd += ["-stub-run", "-profile", "stubrun"]
    if args.report:
        nf_cmd += ["-with-report", f"{args.report}.report.html", "-with-timeline", f"{args.report}.timeline.html"]
    else:
        nf_cmd += ["-with-report", f"{output}/run.report.html", "-with-timeline", f"{output}/run.timeline.html"]
    
    nf_cmd += ["-with-trace", f"{output}/run.trace.txt"]
    # if output directory does not exist, it will be created
    if not os.path.exists(output):
        os.makedirs(output)
    params_file = Path(output) / "run_params.yaml"
    nf_cmd += ["-params-file", str(params_file)]

    if args.dry_run:
        print(" ".join(map(str, nf_cmd)))
    else:
        with open(params_file, 'w') as f:
            yaml.dump(task_params, f)
            f.flush()
        if args.verbosity >= VERBOSITY_VERBOSE:
            print(" ".join(map(str, nf_cmd)))
        resume_file = Path(output) / "resume.sh"
        with open(resume_file, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write(" ".join(map(str, nf_cmd)))
            f.write(" -resume")
            if os.environ.get('NXF_WORK'):
                f.write(" -work-dir " + os.environ['NXF_WORK'])
            f.write("\n")
        try:
            subprocess.run(nf_cmd, check=True, capture_output=(args.verbosity <= VERBOSITY_QUIET), text=True)
        except subprocess.CalledProcessError as e:
            print(e.stderr)
            print(f"To resume execution, run: sh {resume_file}")
            if files_to_delete:
                print(f"Don't forget to delete file(s) {' '.join(files_to_delete)}")
            return 1
    if not args.dry_run and not args.stub_run:
        print_statistics(output)
    # TODO: Use try-finally to delete the metadata file
    for f in files_to_delete:
        os.unlink(f)

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))

import os
import shutil
import subprocess as sp

from Tools import TryMkDirs


def RunBUSCO(buscopath, lineagepath, gene_sets):
    """
    Runs BUSCO analysis on every protein set and writes output files to BUSCO folder.
    """
    bdir = "./busco"

    # Don't rewrite work directory if already there.
    TryMkDirs(bdir)

    for gene_set in gene_sets:
        wd = gene_set.split("/")[-1]
        cmd = [buscopath, "-i", gene_set,  "-l", lineagepath, "-o", "{0}.busco".format(wd), "-m", "prot"]
        print "Running BUSCO"
        sp.call(cmd)
        shutil.move("run_{0}.busco".format(wd), bdir)
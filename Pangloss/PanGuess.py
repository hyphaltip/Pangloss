# -*- coding: utf-8 -*-
"""
PanGuess: gene prediction for Pangloss.

PanGuess is a gene prediction pipeline used to generate protein and genomic
location data for pangenomic analysis of eukaryotes using Pangloss. PanGuess
is a component of Pangloss and can be used in conjuction with Pangloss to determine
pangenomic structure of species of interest based on microsynteny using only genomic data
and a reference protein set.

PanGuess can also be run without downstream PanOCT or other analyses by running the master script with
the flag --pred_only.

Recent changes:
    v0.6.0 (May 2019)
    - Changed cutoff in Exonerate gene model prediction from sequence cutoff to potential alignment score cutoff.
    - Added in paths for Exonerate, GeneMark-ES and TransDecoder from config file.
    - Changed the way get_sequence_from_GTF.pl is called.
    - Moved cores check out of PanGuess and into PanGuessHandler in master script.

    v0.5.0 (February 2019)
    - Changed way that contig ID is extracted in TransDecoderGTFToAttributes from a row.split method
      to a regex match to account for contig IDs that contain underscores.
    - Changed way that global_locs is extracted in TransDecoderGTFToAttributes by just taking last two
      elements of a row.split method list.

    v0.4.0 (February 2019)
    - Made Exonerate prediction optional.
    - Slight changes to ExonerateGene.
    - Improved logging, tying in with other modules and master script.

    v0.3.0 (January 2019)
    - Massive rewrite, improved code in a number of ways.
    - ExonerateGene now includes nucleotide sequence data for yn00 analysis.
    
    v0.2.0 (March 2018)
    - Defined ExonerateGene as class, moved some functions to Tools module.
    - Better integrated codebase with Pangloss.
    - Removed other old functions.

    v0.1.0 (Winter 2017)
    - Initial version.

Written by Charley McCarthy, Genome Evolution Lab, Department of Biology,
Maynooth University in 2017-2019 (Charley.McCarthy@nuim.ie).
"""

from __future__ import division

import logging
import multiprocessing as mp
import os
import re
import shutil
import subprocess as sp
import sys
import tarfile
from csv import reader
from glob import glob

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

from Tools import ExonerateCmdLine, LocationOverlap, Pairwise, TryMkDirs  # get_gene_lengths


def LengthOverlap(gene, ref_lengths):
    if gene:
        longest = max(ref_lengths[gene.ref.split("=")[1]], len(gene.called))
        shortest = min(ref_lengths[gene.ref.split("=")[1]], len(gene.called))
        overlap = shortest / longest
        if overlap >= 0.5:
            return True
        else:
            return False
    else:
        return False


def MakeWorkingDir(workdir):
    """
    Tries to make work directory if not already present.
    """
    # Don't rewrite work directory if already there.
    TryMkDirs(workdir)


def BuildRefSet(workdir, ref):
    """
    Build temporary set of reference proteins. It's faster to run Exonerate by splitting
    up the dataset into individual files and running them as separate queries against
    the genome than as a full file.
    """
    # Make folder for reference proteins, if not already present.
    ref_folder = "{0}/ref".format(workdir)
    TryMkDirs(ref_folder)

    # Split user-provided reference set into individual proteins (have to do this).
    ref_db = SeqIO.index(ref, "fasta")
    logging.info("PanGuess: Building reference protein sequence dataset.")
    for seq in ref_db:
        SeqIO.write(ref_db[seq], "{0}/{1}.faa".format(ref_folder, ref_db[seq].id), "fasta")
    ref_db.close()


def BuildExonerateCmds(workdir, ex_path, genome):
    """
    Generate list of exonerate commands to run through multiprocessing.
    """
    # List of commands.
    exon_cmds = []

    # Generate and return commands.
    logging.info("PanGuess: Building set of Exonerate commands.")
    for prot in glob("{0}/ref/*.faa".format(workdir)):
        exon_cmds.append([ex_path, "--model", "protein2genome",
                          "-t", genome, "-q", prot, "--percent", "90",  "--bestn", "1"])
    return exon_cmds


def RunExonerate(cmds, cores):
    """
    Farm list of exonerate commands to CPU threads using multiprocessing.
    
    Returns an unordered list of ExonerateGene instances. Default number of
    threads = (number of cores on computer - 1).
    """
    # Farm out Exonerate processes, wait for all to finish and merge together.
    logging.info("PanGuess: Running Exonerate searches on {0} threads".format(cores))
    farm = mp.Pool(processes=int(cores))
    genes = farm.map(ExonerateCmdLine, cmds)
    farm.close()
    farm.join()

    # Return predicted genes (ignore empty results).
    return [gene for gene in genes if gene]


def GetExonerateAttributes(exonerate_genes, tag):
    """
    Extract attributes from ExonerateGene data. Is somewhat redundant, but makes merging
    Exonerate and GeneMark-ES calls easier downstream.
    """
    # Master list of attributes.
    exonerate_attributes = []

    # Loop through called genes and extract info.
    for gene in exonerate_genes:
        att_column = ("{0};{1};{2}".format(gene.ref, gene.internal_stop, gene.introns))

        # Add to master list.
        exonerate_attributes.append([gene.contig_id, gene.id, gene.locs[0], gene.locs[1], att_column, tag])

    # Return separate Exonerate attributes object.
    logging.info("PanGuess: Identified {0} gene model attributes via Exonerate.".format(len(exonerate_attributes)))
    return exonerate_attributes


def RunGeneMark(genome, gm_path, gm_branch, cores):
    """
    Run GeneMark-ES on given genome, with optional arguments for fungal-specific
    prediction models and number of cores.
    """
    # Get path for GTF extraction script.
    gtf_path = os.path.dirname(os.path.realpath(sys.argv[0])) + "/get_sequence_from_GTF.pl"

    # Run GeneMark-ES and extract data.
    if gm_branch:
        logging.info("PanGuess: Running GeneMark-ES on {0} threads with branching model.".format(cores))
        sp.call([gm_path, "--ES", "--fungus", "--cores", cores, "--sequence", genome])
    else:
        logging.info("PanGuess: Running GeneMark-ES on {0} threads.".format(cores))
        sp.call([gm_path, "--ES", "--cores", cores, "--sequence", genome])
    sp.call([gtf_path, "genemark.gtf", genome])

    # Return CSV object to convert into attribute data.
    return reader(open("genemark.gtf"), delimiter="\t")


def GeneMarkGTFConverter(gtf, tag):
    """
    Convert a GeneMark-produced GTF/GFF file into an attributes "object" for easier
    merging with the predictions from exonerate and TransDecoder downstream.
    """
    # Holding converted attributes and gene model info.
    attributes = []
    locs = []
    exon_count = 0

    # Pairwise loop over GTF file, start extracting info.
    for row, next_row in Pairwise(gtf):
        if next_row is not None:
            if row[8].split("\"") == next_row[8].split("\""):
                if row[2] == "exon":
                    exon_count = exon_count + 1
                locs = locs + [int(row[3]), int(row[4])]

            # End of info for current gene, get everything together and reset variables.
            else:
                if row[2] == "exon":
                    exon_count = exon_count + 1
                locs = locs + [int(row[3]), int(row[4])]
                contig_id = row[0].split(" ")[0]
                gene_id = row[8].split("\"")[1]
                annotations = "GeneMark={0};IS=False;Introns={1}".format(gene_id, exon_count - 1)
                attributes.append([contig_id, gene_id, min(locs), max(locs),
                                   annotations, tag])
                locs = []
                exon_count = 0

        # End of file.
        else:
            if row[2] == "exon":
                exon_count = exon_count + 1
            locs = locs + [int(row[3]), int(row[4])]
            contig_id = row[0].split(" ")[0]
            gene_id = row[8].split("\"")[1]
            annotations = "GeneMark={0};IS=False;Introns={1}".format(gene_id, exon_count - 1)
            attributes.append([contig_id, gene_id, min(locs), max(locs),
                               annotations, tag])

    # Return sorted GeneMark-ES attributes.
    logging.info("PanGuess: Converted and sorted {0} GeneMark-ES attributes.".format(len(attributes)))
    return sorted(attributes, key=lambda x: (x[0], int(x[2])))


def MergeAttributes(first_attributes, second_attributes):
    """
    Return called genes from two methods that do not overlap. Done for both adding
    Exonerate and GeneMark-ES calls together, and then later when adding TransDecoder
    calls into the previous set of calls.
    """
    # Merge calls and sort by contig ID and start codon position.
    unique_calls = first_attributes + second_attributes
    unique_calls.sort(key=lambda x: (x[0], int(x[2])))
    to_remove = []

    # Go through all calls and earmark overlapping/redundant calls for removal.
    for call, next_call in Pairwise(unique_calls):
        if next_call:
            overlap = LocationOverlap(call, next_call)
            if overlap:
                to_remove.append(overlap[1])

    # Return all unique and non-overlapped calls.
    logging.info("PanGuess: Found {0} attributes to remove. Merging all other attributes.".format(len(to_remove)))
    return [call for call in unique_calls if call[1] not in to_remove]


def MoveGeneMarkFiles(workdir, genome):
    """
    Handles temporary folders/files created by GeneMark-ES.
    """
    # GeneMark-ES produces these filenames for each genome run.
    to_move = ["data", "info", "output", "run", "gmes.log", "run.cfg",
               "prot_seq.faa", "nuc_seq.fna", "genemark.gtf"]

    # Attempt to make GeneMark-ES temporary file folder if not extant.
    gmes = "{0}/gmes/{1}/".format(workdir, genome)
    TryMkDirs(gmes)

    # Move all files and folders to new folder.
    logging.info("PanGuess: Moving/Removing GeneMark-ES temporary files and folders.")
    for f in to_move:
        if os.path.isdir(f):
            if not os.path.isdir("{0}/{1}".format(gmes, f)):
                shutil.move(f, gmes)
            else:
                shutil.rmtree(f)
        elif os.path.isfile(f):
            if not os.path.isfile("{0}/{1}".format(gmes, f)):
                shutil.move(f, gmes)
            else:
                os.remove(f)


def ExtractNCR(attributes, genome):
    """
    Generate noncoding sequences from a genome by slicing around known coordinates.
    """
    # List of non-coding regions of genome.
    ncr = []

    # Parse genome file.
    logging.info("PanGuess: Parsed genome file for non-coding region extraction.")
    db = SeqIO.parse(open(genome), "fasta")

    # Loop over every contig/chromosome in the genome.
    for seq in db:
        coding = filter(lambda x: x[0] == seq.id, attributes)
        for gene, next_gene in Pairwise(coding):
            if coding.index(gene) == 0:
                if gene[2] != 0:
                    extract_id = seq.id + "_NCR_0_{0}".format(gene[2] - 1)
                    extract = seq.seq[0:gene[2] - 2]
                    ncr.append(">{0}\n{1}\n".format(extract_id, extract))
            elif next_gene is None:
                extract_id = seq.id + "_NCR_{0}_{1}".format(gene[3] + 1, len(seq) + 1)
                extract = seq.seq[gene[3]:]
                ncr.append(">{0}\n{1}\n".format(extract_id, extract))
            else:
                extract_id = seq.id + "_NCR_{0}_{1}".format(gene[3] + 1, next_gene[2] - 1)
                extract = seq.seq[gene[3]:next_gene[2] - 2]
                ncr.append(">{0}\n{1}\n".format(extract_id, extract))

    # Return list of NCR sequences.
    logging.info("PanGuess: Extracted {0} NCR sequences from {1}.".format(len(ncr), genome))
    return ncr


def RunTransDecoder(ncr, tp_path, tl_path, workdir, genome, td_len):
    """
    Run the two TransDecoder commands via the command line.
    """
    # Try to make a directory for TransDecoder. Might as well do it now.
    tdir = "{0}/td/{1}/".format(workdir, genome)
    TryMkDirs(tdir)

    # Write NCRs to FASTA file
    with open("{0}/NCR.fna".format(tdir), "w") as outfile:
        for line in ncr:
            outfile.write(line)

    # Run both TransDecoder processes sequentially.
    sp.call([tl_path, "-t", "{0}/NCR.fna".format(tdir), "-m", "{0}".format(td_len)])
    sp.call([tp_path, "-t", "{0}/NCR.fna".format(tdir), "--single_best_only"])

    # Return the TransDecoder directory for MoveTransDecoderFiles.
    return tdir


def MoveTransDecoderFiles(tdir):
    """
    Move all temporary TransDecoder files and folders to the TransDecoder directory.
    """
    # List of TransDecoder temporary files to move.
    to_move = glob("NCR*") + glob("pipeliner*")

    # Move all files named "NCR*" and "pipeliner*".
    for f in to_move:
        if os.path.isdir(f):
            if not os.path.isdir("{0}/{1}".format(tdir, f)):
                shutil.move(f, tdir)
            else:
                shutil.rmtree(f)
        elif os.path.isfile(f):
            if not os.path.isfile("{0}/{1}".format(tdir, f)):
                shutil.move(f, tdir)
            else:
                os.remove(f)


def TransDecoderGTFToAttributes(tdir, tag):
    """
    Extract genomic attributes information from TransDecoder GTF file. A bit different
    from our GeneMark GTF parser in that TransDecoder gene sections are seperated in
    the latter GTF file by newline, which makes some things easier (and some things
    harder).
    
    Note: sometimes TransDecoder will add comment lines into a GTF file, hence why we
    have a check that a given line has the standard 9 columns.
    """
    # Reader object and master lists/variables.
    gtf = reader(open("{0}/NCR.fna.transdecoder.gff3".format(tdir)), delimiter="\t")
    attributes = []
    locs = []
    exon_count = 0
    contig_id = ""
    gene_id = ""
    annotations = ""
    cregex = r"(.*)_NCR_"

    # Pairwise loop, start extracting info if line in VALID GTF format.
    for row, next_row in Pairwise(gtf):
        if next_row is not None:
            if row:
                if len(row) == 9:
                    contig_id = re.match(cregex, row[0]).group()[:-5]
                    global_locs = map(int, row[0].split("_")[-2:])
                    if row[2] == "exon":
                        exon_count = exon_count + 1
                    if row[2] == "CDS":
                        relative_locs = map(int, row[3:5])
                        start = global_locs[0] + relative_locs[0] - 1
                        stop = global_locs[0] + relative_locs[1] - 1
                        locs = [start, stop]
                        gene_id = row[-1].split(";")[1].strip("Parent=")
                        annotations = "TransDecoder={0};IS=False;Introns={1}".format(
                            gene_id, exon_count - 1)

                # Ignore comment line.
                else:
                    pass

            # End of gene info, add attributes into master list and reset variables.
            else:
                attributes.append([contig_id, gene_id, min(locs), max(locs), annotations, tag])
                locs = []
                exon_count = 0

        # End of file.
        else:
            attributes.append([contig_id, gene_id, min(locs), max(locs), annotations, tag])
            locs = []
            exon_count = 0

    # Return sorted TransDecoder attributes.
    return sorted(attributes, key=lambda x: (x[0], int(x[2])))


def ConstructGeneModelSets(attributes, exonerate_genes, workdir, genome, tag):
    """
    Build completed gene model set for genome from our three sources.
    """
    # Temporary gene/protein sets from GeneMark-ES and TransDecoder.
    gm_prot_db = SeqIO.index("{0}/gmes/{1}/prot_seq.faa".format(workdir, genome), "fasta")
    gm_nucl_db = SeqIO.index("{0}/gmes/{1}/nuc_seq.fna".format(workdir, genome), "fasta")
    td_prot_db = SeqIO.index("{0}/td/{1}/NCR.fna.transdecoder.pep".format(workdir, genome), "fasta")
    td_nucl_db = SeqIO.index("{0}/td/{1}/NCR.fna.transdecoder.cds".format(workdir, genome), "fasta")

    # Master lists.
    prot_models = []
    nucl_models = []

    # Try to make a directory for protein sets.
    sdir = "{0}/sets".format(workdir)
    TryMkDirs(sdir)


    # Loop over attributes, extract gene from given source based on parent method.
    for gene in attributes:
        if gene[4].startswith("TransDecoder"):
            prot_seq = td_prot_db[gene[1]]
            nucl_seq = td_nucl_db[gene[1]]
            prot_seq.id = "{0}|{1}_{2}_{3}".format(tag, gene[0], gene[2], gene[3])
            nucl_seq.id = prot_seq.id
            gene[1] = prot_seq.id
            prot_models.append(prot_seq)
            nucl_models.append(nucl_seq)
        elif gene[4].startswith("GeneMark"):
            prot_seq = gm_prot_db[gene[1]]
            nucl_seq = gm_nucl_db[gene[1]]
            prot_seq.id = "{0}|{1}_{2}_{3}".format(tag, gene[0], gene[2], gene[3])
            nucl_seq.id = prot_seq.id
            gene[1] = prot_seq.id
            prot_models.append(prot_seq)
            nucl_models.append(nucl_seq)
        if gene[4].startswith("Exonerate"):
            match = filter(lambda x: x.id == gene[1], exonerate_genes)
            prot_seq = SeqRecord(Seq(match[0].prot), id=match[0].id)
            nucl_seq = SeqRecord(Seq(match[0].nucl), id=match[0].id)
            prot_seq.id = "{0}|{1}".format(tag, prot_seq.id)
            nucl_seq.id = "{0}|{1}".format(tag, nucl_seq.id)
            gene[1] = prot_seq.id
            prot_models.append(prot_seq)
            nucl_models.append(nucl_seq)

    # Write protein sequences to file.
    with open("{0}/{1}.faa".format(sdir, tag), "w") as outpro:
        SeqIO.write(prot_models, outpro, "fasta")

    # Write nucleotide sequences to file.
    with open("{0}/{1}.nucl".format(sdir, tag), "w") as outnuc:
        SeqIO.write(nucl_models, outnuc, "fasta")

    # Write attributes to file.
    with open("{0}/{1}.attributes".format(sdir, tag), "w") as outatt:
        for line in attributes:
            outatt.write("\t".join(str(el) for el in line) + "\n")


def TarballGenePredictionDirs(workdir, genome):
    """
    Compress temporary GeneMark-ES and Transdecoder folders into tar.gz files. VERY slow.
    """
    # Tarball genome's GeneMark-ES folder, remove uncompressed copy.
    with tarfile.open("{0}/gmes/{1}.tar.gz".format(workdir, genome), "w:gz") as genemark_tar:
        for root, dirs, files in os.walk("{0}/gmes/{1}".format(workdir, genome)):
            for f in files:
                genemark_tar.add(os.path.join(root, f))
    shutil.rmtree("{0}/gmes/{1}".format(workdir, genome))

    # Tarball genome's TransDecoder folder, remove uncompressed copy.
    with tarfile.open("{0}/td/{1}.tar.gz".format(workdir, genome), "w:gz") as td_tar:
        for root, dirs, files in os.walk("{0}/td/{1}".format(workdir, genome)):
            for f in files:
                td_tar.add(os.path.join(root, f))
    shutil.rmtree("{0}/td/{1}".format(workdir, genome))

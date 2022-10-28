"""Convert Ensembl BMTSV files to minimal TSVs for Ideogram.js gene caches

Ideogram uses cached gene data to drastically simplify and speed up rendering.
"""

import argparse
import codecs
import csv
import gzip
import os
import re
import sys
import urllib.request
from urllib.parse import quote

# Enable importing local modules when directly calling as script
if __name__ == "__main__":
    cur_dir = os.path.join(os.path.dirname(__file__))
    sys.path.append(cur_dir + "/..")

from lib import download
from gene_cache import trim_id, detect_prefix, fetch_gff, parse_gff_info_field, fetch_interesting_genes

# Organisms configured for gene caching, and their genome assembly names
assemblies_by_org = {
    "Homo sapiens": "GRCh38",
    "Mus musculus": "GRCm38",
    "Danio rerio": "GRCz11",
    "Gallus gallus": "GRCg6a",
    "Rattus norvegicus": "Rnor_6.0",
    "Pan troglodytes": "Pan_tro_3.0",
    "Macaca fascicularis": "Macaca_fascicularis_5.0",
    "Macaca mulatta": "Mmul_10",
    "Canis lupus familiaris": "CanFam3.1",
    "Felis catus": "Felis_catus_9.0",
    "Equus caballus": "EquCab3.0",
    "Bos taurus": "ARS-UCD1.2",
    "Sus scrofa": "Sscrofa11.1",
    # "Anopheles gambiae": "AgamP4.51",
    "Caenorhabditis elegans": "WBcel235",
    "Drosophila melanogaster": "BDGP6.28"
}

ranked_genes_by_organism = {
    "Homo sapiens": "gene-hints.tsv",
    "Mus musculus": "pubmed-citations.tsv",
    "Rattus norvegicus": "pubmed-citations.tsv",
    "Canis lupus familiaris": "pubmed-citations.tsv",
    "Felis catus": "pubmed-citations.tsv",
}

biotypes = {}

# metazoa = {
#     "Anopheles gambiae".AgamP4.51.bmtsv.gz  "
# }

def fetch_slim_transcript_ids(organism, output_dir="data/", reuse_bmtsv=True):
    gscache = GeneStructureCache(output_dir, reuse_bmtsv)
    slim_transcripts = gscache.fetch_transcripts(organism)
    return slim_transcripts

def get_bmtsv_url(organism):
    """Get URL to BMTSV file
    E.g. https://www.ensembl.org/biomart/martservice?query=%3C%21DOCTYPE%20Query%3E%3CQuery%20formatter%3D%22TSV%22%20header%3D%220%22%20uniqueRows%3D%220%22%20count%3D%22%22%20datasetConfigVersion%3D%220.6%22%3E%3CDataset%20name%3D%22hsapiens_gene_ensembl%22%20interface%3D%22default%22%3E%3CFilter%20name%3D%22transcript_is_canonical%22%20excluded%3D%220%22/%3E%3CAttribute%20name%3D%22ensembl_transcript_id%22%20/%3E%3C/Dataset%3E%3C/Query%3E
    """

    # E.g. "Homo sapiens" -> "hsapiens"
    split_org = organism.split()
    brief_org = (split_org[0][0] + split_org[1]).lower()

    query = quote((
        '<!DOCTYPE Query>' +
        '<Query formatter="TSV" header="0" uniqueRows="0" count="" datasetConfigVersion="0.6">' +
        '<Dataset name="' + brief_org + '_gene_ensembl" interface="default">' +
            '<Filter name="transcript_is_canonical" excluded="0"/>' +
            '<Attribute name="ensembl_transcript_id" />' +
        '</Dataset>' +
        '</Query>'
    ).encode("utf-8"))
    url = f"https://www.ensembl.org/biomart/martservice?query={query}"
    return url

def parse_bmtsv_info_field(info):
    """Parse a BMTSV "INFO" field into a dictionary
    Example INFO field:
    gene_id "ENSMUSG00000102628"; gene_version "2"; gene_name "Gm37671"; gene_source "havana"; gene_biotype "TEC";
    """
    fields = [f.strip() for f in info.split(';')][:-1]
    kvs = [f.split("=") for f in fields]
    info_dict = {}
    for kv in kvs:
        info_dict[kv[0]] = kv[1].strip('"')
    return info_dict

def trim_bmtsv(bmtsv_path):
    """Parse BMTSV into a list of slim Ensembl canonical transcript IDs
    """
    print(f"Parsing and trimming BMTSV: {bmtsv_path}")
    slim_transcripts = []
    prefix = None

    with open(bmtsv_path) as file:
        reader = csv.reader(file, delimiter="\t")
        for row in reader:
            id = row[0]

            if prefix == None:
                prefix = detect_prefix(id)
            slim_id = trim_id(id, prefix)

            slim_transcripts.append(slim_id)

    return [slim_transcripts, prefix]

def parse_feature(gff_row, canonical_ids):
    """Return parsed transcript-related feature from CSV-reader-split row of GFF file"""
    feat_type = gff_row[2]

    # feature types that are modeled as genes in Ensembl, i.e.
    # that have an Ensembl accession beginning ENSG in human
    loose_transcript_types = [
        "mRNA", "five_prime_UTR", "three_prime_UTR", "exon"

        # TODO:
        # - Confirm these make sense to include
        # - Confirm handling
        # "miRNA", "ncRNA", "ncRNA_gene", "rRNA",
        # "scRNA", "snRNA", "snoRNA", "tRNA"
    ]
    if feat_type not in loose_transcript_types:
        return None

    info = gff_row[8]
    # print("info", info)
    info = parse_gff_info_field(info)

    transcript_id = None
    if "ID" in info:
        transcript_id = info["ID"].split('transcript:')[1]
    elif "Parent" in info:
        transcript_id = info["Parent"].split("transcript:")[1]

    # is_canonical = transcript_id not in canonical_ids
    # if transcript_id not in canonical_ids:
    #     return None

    chr = gff_row[0]
    start = gff_row[3]
    stop = gff_row[4]

    structure = [transcript_id, feat_type, chr, start, stop]

    # 9	ensembl_havana	mRNA	112750760	112874987	.	+	.	ID=transcript:ENST00000374232;Parent=gene:ENSG00000148158;Name=SNX30-201;biotype=protein_coding;ccdsid=CCDS43865.1;tag=basic;transcript_id=ENST00000374232;transcript_support_level=5 (assigned to previous version 7);version=8
    # 9	ensembl_havana	five_prime_UTR	112750760	112751001	.	+	.	Parent=transcript:ENST00000374232
    # 9	ensembl_havana	exon	112750760	112751157	.	+	.	Parent=transcript:ENST00000374232;Name=ENSE00001462855;constitutive=0;ensembl_end_phase=0;ensembl_phase=-1;exon_id=ENSE00001462855;rank=1;version=4
    if feat_type == "mRNA":
        # + (forward) or - (reverse)
        strand = gff_row[6]
        name = info["Name"]
        gene_id = info["Parent"].split("gene:")[1]
        biotype = info["biotype"]
        if biotype not in biotypes:
            biotypes[biotype] = 1
        transcript_support_level = info.get("transcript_support_level", "")
        return structure + [strand, name, gene_id, biotype, transcript_support_level]

    elif feat_type in ["five_prime_UTR", "three_prime_UTR"]:
        return structure

    elif feat_type == "exon":
        transcript_id = info["Parent"].split("transcript:")[1]
        # name = info["Name"]
        constitutive = info["constitutive"]
        ensembl_phase = info["ensembl_phase"]
        exon_id = info["exon_id"]
        rank = info["rank"]
        return structure + [
            exon_id, constitutive, ensembl_phase, rank
        ]

    return None

def parse_bmtsv(bmtsv_path):
    """Parse BMTSV into a set of Ensembl canonical transcript IDs
    """
    print(f"Parsing BMTSV: {bmtsv_path}")
    transcript_ids = []

    with open(bmtsv_path) as file:
        reader = csv.reader(file, delimiter="\t")
        for row in reader:
            id = row[0]
            transcript_ids.append(id)

    return set(transcript_ids)

def get_length(start, stop):
    length = str(int(stop) - int(start))
    return length

def compress_biotype(biotype, biotype_map):
    if biotype:
        return biotype_map[biotype]
    else:
        last = sort(list(biotype_map.values()))[-1]
        new_last = last + 1


subpart_map = {
    'five_prime_UTR': '0',
    'exon': '1',
    'three_prime_UTR': '2',
}
def parse_transcript_subpart(raw_subpart, mrna_start):
    # transcript_id, feat_type, chr, start, stop, exon_id, constitutive, ensembl_phase, rank
    # ENST00000641515 exon    1       65419   65433   ENSE00003812156 0       -1      1

    subpart_type_compressed = ""
    subpart_type = raw_subpart[1]
    if subpart_type in subpart_map:
        subpart_type_compressed = subpart_map[subpart_type]
    else:
        print('subpart_type: ', subpart_type)

    # Use mRNA-relative start coordinate
    start = get_length(mrna_start, raw_subpart[3])

    # Length of this exon
    length = get_length(raw_subpart[3], raw_subpart[4])

    return [subpart_type_compressed, start, length]

def parse_mrna(raw_mrna, biotypes_list):
    # transcript_id, feat_type, chr, start, stop, strand, name, gene_id, biotype, transcript_support_level
    # ENST00000616016 mRNA    1       925741  944581  SAMD11-210      ENSG00000187634 protein_coding  5
    transcript_id = raw_mrna[0]

    chr = raw_mrna[2]
    start = raw_mrna[3]
    stop = raw_mrna[4]
    length = get_length(start, stop)

    strand = raw_mrna[5]

    name = raw_mrna[6]
    # gene_id = raw_mrna[7]
    biotype_compressed = str(biotypes_list.index(raw_mrna[8]))
    if biotype_compressed == "0": biotype_compressed = ""

    # return [transcript_id, chr, start, length, name, gene_id, biotype]
    return [[transcript_id, name, biotype_compressed, strand], start]

def build_structures(structures_by_id):
    biotypes_list = list(biotypes.keys())

    structures = []
    for id in structures_by_id:
        structure_lists = structures_by_id[id]
        if structure_lists[0][1] != "mRNA":
            continue
        structure = []
        [mrna, mrna_start] = parse_mrna(structure_lists[0], biotypes_list)
        structure += mrna

        for structure_list in structure_lists[1:]:
            subpart = parse_transcript_subpart(structure_list, mrna_start)
            joined_subpart = ";".join(subpart)

            # Almost all subparts are exons
            # We can key these space-efficiently via absence of key
            if joined_subpart[0:2] == "1;":
                joined_subpart = joined_subpart[2:]

            structure += [joined_subpart]

        structures.append(structure)

    return structures

def parse_structures(canonical_ids, gff_path, gff_url):
    """Parse gene structures from transcripts in GFF file

    Genes usually have multiple transcripts, one of which is "canonical",
    meaning it's considered the representative reference transcript.
    Docs: https://www.ensembl.org/info/genome/genebuild/canonical.html

    Parts of a transcript that comprise "gene structure" here:
        * Exons: regions of gene not removed by RNA splicing
        * 3'-UTR: Three prime untranslated region; start region
        * 5'-UTR: Fix prime untranslated region; end region

    (Introns are the regions between 3'- and 5'-UTRs that are not exons.
    These are implied in the structure, and not modeled explicitly.)
    """

    structures_by_id = {}
    prev_feature = None
    i = 0
    with open(gff_path) as file:
        reader = csv.reader(file, delimiter="\t")
        for row in reader:
            i += 1

            if row[0][0] == "#":
                # Skip header
                continue

            feature = parse_feature(row, canonical_ids)

            if feature == None:
                continue

            if (i % 50000 == 0):
                print(f"On entry {i}")
                print(feature)

            # if i > 100000:
            #     # return structures_by_id
            #     break

            id = feature[0]
            feat_type = feature[1]
            [chr, start, stop] = feature[2:5]

            if id in structures_by_id:
                structures_by_id[id].append(feature)
            else:
                structures_by_id[id] = [feature]

            # [chr, start, stop, id, symbol, desc] = parsed_gene
            # length = str(int(stop) - int(start))

            # if prefix == None:
            #     prefix = detect_prefix(id)
            # slim_id = trim_id(id, prefix)

            # slim_genes.append([chr, start, length, slim_id, symbol, desc])

    structures = build_structures(structures_by_id)
    return structures

def sort_structures(structures, organism, canonical_ids):
    ranks = fetch_interesting_genes(organism)
    print('ranks[0:10]')
    print(ranks[0:10])
    print('structures[0:10]')
    print(structures[0:10])
    sorted_structures = []

    structures_with_genes = []
    for structure in structures:
        # E.g. FOO-BAR-404 -> FOO-BAR
        gene = "".join(structure[1].split('-')[:-1])
        structures_with_genes.append([gene] + structure)


    structs = structures_with_genes
    structs = sorted(
        structs,
        key=lambda s: s[1] in canonical_ids, reverse=True
    )
    structures_with_genes = structs


    # Sort genes by interest rank, and put unranked genes last
    trimmed_structs = sorted(
        structures_with_genes,
        key=lambda s: ranks.index(s[0]) if s[0] in ranks else 1E10,
    )

    # Omit strand for non-canonical transcripts
    tmp_structs = []
    strands_by_gene = {}
    for structure in trimmed_structs:
        gene = structure[0]
        if gene in strands_by_gene:
            structure[4] = ''
        else:
            strands_by_gene[gene] = structure[4]
        tmp_structs.append(structure)
    trimmed_structs = tmp_structs

    sorted_structures = []
    for structure in trimmed_structs:
        sorted_structures.append(structure[2:])


    # structs =
    # for id in structures_by_id:
    #     structs = structures_by_id[id]
    #     structs = sorted(structs, key=lambda s: s[0] in canonical_ids)
    #     structs = sorted(structs, key=lambda s: s[0] in canonical_ids)

    print('sorted_structures[0:10]')
    print(sorted_structures[0:10])

    return sorted_structures

def compress_structures(structures):
    compressed_structures = []
    tmp_structs = []

    print("Compress subpart start coordinates to be relative to previous subpart's")
    for (i, structure) in enumerate(structures):
        compressed_structure = structure[0:3]
        subparts = structure[3:]
        for (j, subpart) in enumerate(subparts):
            if j == 0:
                compressed_structure.append(subpart)
                continue
            subpart = subpart.split(";")
            prev_subpart = subparts[j - 1].split(";")
            utr = len(subpart) == 3
            start = int(subpart[0] if not utr else subpart[1])
            length = int(subpart[1] if not utr else subpart[2])
            prev_utr = len(prev_subpart) == 3
            prev_start = int(prev_subpart[0] if not prev_utr else prev_subpart[1])
            # prev_len = int(prev_subpart[1] if prev_utr else prev_subpart[2])
            relative_start = start - prev_start
            utr_prefix = "" if not utr else f"{subpart[0]};"
            compressed_subpart = f"{utr_prefix}{relative_start};{length}"
            compressed_structure.append(compressed_subpart)
        tmp_structs.append(compressed_structure)
    compressed_structures = tmp_structs

    print(
        "Compress UTR direction, e.g. 2;0;283 -> U0;283 and 0;186;48 -> U186;48",
        "5' or 3' can be deduced via strand and presence of intervening CDS's"
    )
    tmp_structs = []
    for (i, structure) in enumerate(compressed_structures):
        compressed_structure = structure[0:3]
        subparts = structure[3:]
        for (j, subpart) in enumerate(subparts):
            split_subpart = subpart.split(";")
            if len(split_subpart) == 3:
                compressed_subpart = f"U{';'.join(split_subpart[1:])}"
            else:
                compressed_subpart = subpart
            compressed_structure.append(compressed_subpart)
        tmp_structs.append(compressed_structure)
    compressed_structures = tmp_structs

    print(
        "Compress subparts to pointers, when subpart has been seen in current gene",
        "Among pointers, omit any that merely increment the previous"
    )
    seen_parts = {}
    prev_gene = ''
    tmp_structs = []
    i = 0
    for structure in compressed_structures:
        compressed_structure = structure[0:3]
        subparts = structure[3:]
        gene = structure[0].split('-')[0]
        if gene == prev_gene:
            prev_compressed_subpart = None
            sp_offset = 0
            # Processing same gene
            for (j, subpart) in enumerate(subparts):
                if subpart in seen_parts:
                    compressed_subpart = seen_parts[subpart]
                    scs = compressed_subpart.split("_") # "*s*plit *c*ompressed *s*ubpart
                    tx_i = int(scs[0]) # Transcript pointer
                    sp_j = int(scs[1]) # Subpart pointer
                    omit = False
                    if prev_compressed_subpart:
                        # p_tx_i: Previous transcript pointer
                        # p_sp_j: Previous subpart pointer
                        p_tx_i = int(prev_compressed_subpart[0])
                        if sp_offset == 0:
                            p_sp_j = int(prev_compressed_subpart[1])
                        else:
                            p_sp_j = sp_offset
                        # if gene == "ACE2":
                        #     print(
                        #         "compressed_structure, prev_compressed_subpart, tx_i, p_tx_i, sp_j, p_sp_j, sp_offset",
                        #         compressed_structure, prev_compressed_subpart, tx_i, p_tx_i, sp_j, p_sp_j, sp_offset
                        #     )
                        if tx_i - p_tx_i == 0 and sp_j - p_sp_j == 1:
                            # Same transcipt and this subpart index is 1 more than prev subpart index
                            sp_offset = sp_j
                            compressed_subpart = ""
                            omit = True
                    if not omit:
                        sp_offset = 0
                        prev_compressed_subpart = scs

                else:
                    prev_compressed_subpart = None
                    sp_offset = 0
                    compressed_subpart = subpart
                    seen_parts[subpart] = str(i) + '_' + str(j)
                compressed_structure.append(compressed_subpart)
        else:
            # Processing new gene
            i = 0
            prev_gene = gene
            seen_parts = {}
            for (j, subpart) in enumerate(subparts):
                seen_parts[subpart] = str(i) + '_' + str(j)
            compressed_structure = structure
        tmp_structs.append(compressed_structure)
    compressed_structures = tmp_structs

    print("Compress coterminal exons and UTRs, e.g. 0;283  U0;283 -> E0;283")
    tmp_structs = []
    for (i, structure) in enumerate(compressed_structures):
        compressed_structure = structure[0:3]
        subparts = structure[3:]
        for (j, subpart) in enumerate(subparts):
            if j == 0 or len(subpart) > 0 and subpart[0] != "U":
                compressed_structure.append(subpart)
                continue
            prev_subpart = subparts[j - 1]
            if prev_subpart == "":
                compressed_structure.append(subpart)
                continue
            utr_range = subpart[1:] # e.g. U0:283 -> 0:283
            split_subpart = subpart.split(";")
            # gene = structure[0].split('-')[0]
            # if gene == "ACE2": print("split_subpart, prev_subpart, utr_range", split_subpart, prev_subpart, utr_range)
            if prev_subpart == utr_range:
                compressed_structure[-1] = ""
                compressed_subpart = f"E{subpart[1:]}"
            else:
                compressed_subpart = subpart
            compressed_structure.append(compressed_subpart)
        tmp_structs.append(compressed_structure)
    compressed_structures = tmp_structs

    print(
        "Compress lengths at same-index subparts, e.g.",
        "U273  _  69458;194",
        "U738  790  45835;194", # 194 is length of 3rd subpart in both
        "->"
        "U273  _  69458;194",
        "U738  790  45835^", # ";194" -> "^": 75% smaller here, always >= 50%
    )
    tmp_structs = []
    for (i, structure) in enumerate(compressed_structures):
        compressed_structure = structure[0:3]
        subparts = structure[3:]
        for (j, subpart) in enumerate(subparts):
            if i == 0 or j == 0 or len(subpart) == 0:
                compressed_structure.append(subpart)
                continue
            prev_subparts = structures[i - 1][3:]
            if j > len(prev_subparts) - 1:
                compressed_structure.append(subpart)
                continue
            prev_split_subpart = prev_subparts[j].split(";")
            split_subpart = subpart.split(";")
            not_pointers = len(prev_split_subpart) == 2 and len(split_subpart) == 2
            if (
                not_pointers and
                 # main test
                int(prev_split_subpart[1]) - int(split_subpart[1]) == 0
            ):
                compressed_subpart = f"{split_subpart[0]}^"
            else:
                compressed_subpart = subpart
            compressed_structure.append(compressed_subpart)
        tmp_structs.append(compressed_structure)
    compressed_structures = tmp_structs


    print(
        "Compress 0-start coordinates, e.g. 0:283 -> 283",
    )
    tmp_structs = []
    for (i, structure) in enumerate(compressed_structures):
        compressed_structure = structure[0:3]
        subparts = structure[3:]
        for (j, subpart) in enumerate(subparts):
            split_subpart = subpart.split(";")
            if len(split_subpart) == 2 and split_subpart[0][-1] == "0":
                compressed_subpart = split_subpart[0][:-1] + split_subpart[1]
            else:
                compressed_subpart = subpart
            compressed_structure.append(compressed_subpart)
        tmp_structs.append(compressed_structure)
    compressed_structures = tmp_structs

    print(
        "Compress 0-start pointers, e.g. 0_3 -> _3 and 0_0 -> _",
    )
    tmp_structs = []
    for (i, structure) in enumerate(compressed_structures):
        compressed_structure = structure[0:3]
        subparts = structure[3:]
        for (j, subpart) in enumerate(subparts):
            split_subpart = subpart.split("_")
            if len(split_subpart) == 2 and split_subpart[0] == "0":
                if split_subpart[1] == "0":
                    compressed_subpart = "_"
                else:
                    compressed_subpart = f"_{split_subpart[1]}"
            else:
                compressed_subpart = subpart
            compressed_structure.append(compressed_subpart)
        tmp_structs.append(compressed_structure)
    compressed_structures = tmp_structs

    # Trim non-canonical transcript names, e.g. ACE2-208 -> 8
    gene_keys = {}
    tmp_structs = []
    for structure in compressed_structures:
        split_tx_name = structure[0].split('-')
        gene = "".join(split_tx_name[:-1])
        tx_num = int(split_tx_name[-1]) # e.g. 208 in ACE2-208
        if gene in gene_keys:
            ref_tx_base_num = gene_keys[gene]
            trimmed_tx_num = tx_num - ref_tx_base_num # e.g. 208 - 200
            structure[0] = str(trimmed_tx_num) # e.g. 8
        else:
            str_tx_num = str(tx_num)
            highest_digit = int(str_tx_num[0])
            num_digits = len(str(tx_num))
            ref_tx_base_num = highest_digit * (10 ** (num_digits - 1))
            gene_keys[gene] = ref_tx_base_num # e.g. 200
        tmp_structs.append(structure)
    compressed_structures = tmp_structs

    print("Compress coterminal post-exon UTR lengths, e.g. 10782;113  U113 -> 10782;113  U")
    tmp_structs = []
    for (i, structure) in enumerate(compressed_structures):
        compressed_structure = structure[0:3]
        subparts = structure[3:]

        if len(subparts) < 2:
            # print(f"Odd transcript at index {str(i)}, < 2 subparts", structure)
            tmp_structs.append(compressed_structure)
            continue

        prev_subpart = subparts[-2]
        subpart = subparts[-1]

        if len(prev_subpart) > 0 and len(subpart) > 0 and subpart[0] == "U":
            if ";" not in subpart:
                utr_length = subpart[1:]
            else:
                utr_length = subpart.split(";")[1]
            if ";" not in prev_subpart:
                exon_length = prev_subpart[1:]
            else:
                exon_length = prev_subpart.split(";")[1]
            if (
                utr_length.isdigit() and exon_length.isdigit() and
                int(utr_length) - int(exon_length) == 0
            ):
                subpart = "U"
        structure[-1] = subpart
        tmp_structs.append(structure)
    compressed_structures = tmp_structs

    print('Compress "+" to "" in strand column')
    tmp_structs = []
    for structure in compressed_structures:
        strand = structure[2]
        if strand == '+': # Human protein coding genes: 9.9k +, 9.7k -
            structure[2] = ''
        tmp_structs.append(structure)
    compressed_structures = tmp_structs

    # Trim canonical transcript names, e.g. ACE2-201 -> 201
    gene_keys = {}
    tmp_structs = []
    for structure in compressed_structures:
        split_tx_name = structure[0].split('-')
        if len(split_tx_name) > 1:
            structure[0] = f"!{split_tx_name[1]}"
        tmp_structs.append(structure)
    compressed_structures = tmp_structs

    return compressed_structures

class GeneStructureCache():
    """Convert Ensembl BioMart TSVs to compact TSVs for Ideogram.js caches
    """

    def __init__(self, output_dir="data/", reuse_bmtsv=False):
        self.output_dir = output_dir
        self.tmp_dir = "data/"
        self.reuse_bmtsv = reuse_bmtsv

        self.biotype_map = []

        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        if not os.path.exists(self.tmp_dir):
            os.makedirs(self.tmp_dir)

    def fetch_ensembl_biomart_tsv(self, organism):
        """Download an organism's TSV file from Ensembl BioMart
        """
        print(f"Fetching Ensembl BioMart TSV for {organism}")
        url = get_bmtsv_url(organism)
        bmtsv_dir = self.tmp_dir + "bmtsv/"
        if not os.path.exists(bmtsv_dir):
            os.makedirs(bmtsv_dir)
        org_lch = organism.lower().replace(" ", "-")
        bmtsv_path = bmtsv_dir + org_lch + "-transcripts.tsv"
        try:
            download(url, bmtsv_path, cache=self.reuse_bmtsv)
        except urllib.error.HTTPError:
            # E.g. for C. elegans
            url = url.replace("chr.", "")
            download(url, bmtsv_path, cache=self.reuse_bmtsv)
        return [bmtsv_path, url]

    def write(self, structures, organism, gff_url, bmtsv_url):
        """Save fetched and transformed gene data to cache file
        """
        biotypes_list = list(biotypes.keys())
        biotype_keys = ", ".join([
            f"{str(i)} = {key}" for i, key in enumerate(biotypes_list)
        ])

        headers = "\n".join([
            f"## Ideogram.js gene structure cache for {organism}",
            f"## Derived from: {gff_url}",
            f"## Filtered to only canonical transcripts using Ensembl BioMart: {bmtsv_url}",
            f"## features: <subpart_compressed>;<start (transcript-relative)>;<end (transcript-relative)>",
            f"## biotype keys: {biotype_keys}",
            f"## subpart keys: 0 = 5'-UTR, 1 = exon, 2 = 3'-UTR",
            f"# transcript_name\tbiotype_compressed\t\tstrand\tfeatures"
        ]) + "\n"

        # print('structures')
        # print(structures)
        structure_lines = "\n".join(["\t".join(s) for s in structures])
        content = headers + structure_lines

        org_lch = organism.lower().replace(" ", "-")
        output_path = f"{self.output_dir}{org_lch}-gene-structures.tsv.gz"
        with gzip.open(output_path, "wt") as f:
            f.write(content)
        print(f"Wrote gene structure cache: {output_path}")

    def fetch_transcript_ids(self, organism):
        [bmtsv_path, bmtsv_url] = self.fetch_ensembl_biomart_tsv(organism)
        transcript_ids = parse_bmtsv(bmtsv_path)
        return [transcript_ids, bmtsv_url]

    def populate_by_org(self, organism):
        """Fill gene caches for a configured organism
        """
        # [slim_transcripts, prefix] = self.fetch_slim_transcripts(organism)
        [canonical_ids, bmtsv_url] = self.fetch_transcript_ids(organism)

        [gff_path, gff_url] = fetch_gff(organism, self.output_dir, True)

        structures = parse_structures(canonical_ids, gff_path, gff_url)

        sorted_structures = sort_structures(structures, organism, canonical_ids)
        refined_structures = compress_structures(sorted_structures)

        # sorted_slim_genes = sort_by_interest(slim_genes, organism)
        self.write(refined_structures, organism, gff_url, bmtsv_url)

    def populate(self):
        """Fill gene caches for all configured organisms

        Consider parallelizing this.
        """
        # for organism in assemblies_by_org:
        for organism in ["Homo sapiens"]:
            self.populate_by_org(organism)

# Command-line handler
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output-dir",
        help=(
            "Directory to put outcome data.  (default: %(default))"
        ),
        default="data/"
    )
    parser.add_argument(
        "--reuse-bmtsv",
        help=(
            "Whether to use previously-downloaded raw BMTSVs"
        ),
        action="store_true"
    )
    args = parser.parse_args()
    output_dir = args.output_dir
    reuse_bmtsv = args.reuse_bmtsv

    GeneStructureCache(output_dir, reuse_bmtsv).populate()

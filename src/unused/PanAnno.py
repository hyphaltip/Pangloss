import subprocess as sp
from csv import reader
core = {}
noncore = {}


def generate_annotation_dict(ips_file):
	with open(ips_file) as infile:
		annotations_dict = {}
		for line in reader(infile, delimiter="\t"):
			protein = line[0]
			if protein not in annotations_dict:
				annotations_dict[protein] = {}
				annotations_dict[protein]["PFAM"] = {}
				annotations_dict[protein]["IPR"] = {}
				annotations_dict[protein]["GO"] = []
			if line[4]:
				annotations_dict[protein]["PFAM"][line[4]] = line[5]
			if len(line) > 11:
				annotations_dict[protein]["IPR"][line[11]] = line[12]
			if len(line) == 14:
				annotations_dict[protein]["GO"] = annotations_dict[protein]["GO"] + [go for go in line[13].split("|") if go]
	return annotations_dict


def get_strains(core):
	key = core[0]
	strains = [gene.split("|")[0] for gene in core[key]]
	return strains


def get_genome_enrichments(strain, states, annotations, go_terms, go_slim):
	with open("{0}_associations.txt".format(strain), "w") as assocs, open("{0}_population.txt".format(strain), "w") as popl:
		for gene in annotations:
			if gene.startswith(strain):
				if annotations[gene]["GO"]:
					assocs.write("{0}\t{1}\n".format(gene, ";".join(annotations[gene]["GO"])))
					popl.write("{0}\n".format(gene))
	sp.call(["map_to_slim.py", "--association_file={0}_associations.txt".format(strain), go_terms, go_slim], stdout=open("{0}_slim_temp.txt".format(strain), "w"))
	with open("{0}_slim.txt".format(strain), "w") as slim:
		for line in open("{0}_slim_temp.txt".format(strain)).readlines():
			if line.startswith(strain):
				slim.write(line)
	with open("{0}_gained_popl.txt".format(strain), "w") as gained:
		for cluster in states[strain]:
			if cluster[1]:
					member = filter(lambda x: x.startswith(strain), noncore[cluster[0]])[0]
					if member in annotations:
						if annotations[member]["GO"]:
							gained.write("{0}\n".format(member))
	sp.call(["find_enrichment.py", "--pval=0.05", "--method=fdr", "--obo", go_terms, "{0}_gained_popl.txt".format(strain), "{0}_population.txt".format(strain), "{0}_slim.txt".format(strain), "--outfile={0}_enrichment.tsv".format(strain)])


def get_complement_enrichments(core, noncore, annotations, go_terms, go_slim):
	with open("pangenome_associations.txt", "w") as passocs, open("pangenome_population.txt", "w") as panpopl:
		print "Getting association and background population for pangenome..."
		for gene in annotations:
			if annotations[gene]["GO"]:
				passocs.write("{0}\t{1}\n".format(gene, ";".join(annotations[gene]["GO"])))
				panpopl.write("{0}\n".format(gene))
	sp.call(["map_to_slim.py", "--association_file=pangenome_associations.txt", go_terms, go_slim], stdout=open("pangenome_slim_temp.txt", "w"))
	with open("pangenome_slim.txt", "w") as panslim:
		print "Tidying GO Slim file..."
		for line in open("pangenome_slim_temp.txt").readlines():
			if "|" in line:
				panslim.write(line)
	with open("core_population.txt", "w") as corepop:
		print "Getting core study population..."
		for cluster in core:
			for gene in core[cluster]:
				if gene in annotations:
					if annotations[gene]["GO"]:
						corepop.write("{0}\n".format(gene))
	with open("noncore_population.txt", "w") as noncorepop:
		print "Getting noncore study population..."
		for cluster in noncore:
			for gene in noncore[cluster]:
				if gene != "----------":
					if gene in annotations:
						if annotations[gene]["GO"]:
							noncorepop.write("{0}\n".format(gene))
	sp.call(["find_enrichment.py", "--pval=0.05", "--method=fdr", "--obo", go_terms, "core_population.txt", "pangenome_population.txt", "pangenome_slim.txt", "--outfile=core_enrichment.tsv"])
	sp.call(["find_enrichment.py", "--pval=0.05", "--method=fdr", "--obo", go_terms, "noncore_population.txt", "pangenome_population.txt", "pangenome_slim.txt", "--outfile=noncore_enrichment.tsv"])


def main():
	with open("new_matchtable.txt") as infile:
		matchtable = reader(infile, delimiter="\t")
		for row in matchtable:
			if "----------" in row:
				noncore[row[0]] = row[1:]
			else:
				core[row[0]] = row[1:]  # Populating our core dict

#	with open("new_softtable.txt") as infile:
#		matchtable = reader(infile, delimiter="\t")
#		for row in matchtable:
#			softcore[row[0]] = row[1:]  # Populating our core dict

#	with open("new_nontable.txt") as infile:
#		matchtable = reader(infile, delimiter="\t")
#		for row in matchtable:
#			noncore[row[0]] = row[1:]  # Populating our core dict

	print "Loading IPS annotations file..."
	anno_dict = generate_annotation_dict("yeast_ips.txt")
	print "Loaded annotations.\nLoading TNT output..."
	#cluster_states = generate_mapping_dict("tnt.output")
	print "Loaded TNT output."
	get_complement_enrichments(core, noncore, anno_dict, "/Users/cmccarthy/Desktop/go.obo", "/Users/cmccarthy/Desktop/goslim_yeast.obo")


if __name__ == "__main__":
	main()
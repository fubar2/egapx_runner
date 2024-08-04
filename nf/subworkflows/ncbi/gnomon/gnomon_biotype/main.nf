#!/usr/bin/env nextflow
nextflow.enable.dsl=2

include { merge_params } from '../../utilities'

workflow gnomon_biotype {
    take:
        models_files
        splices_files
        denylist 
        gencoll_asn 
        swiss_prot_asn
        lds2_source
        raw_blastp_hits
        parameters  // Map : extra parameter and parameter update
    main:
        default_params = ""
        effective_params = merge_params(default_params, parameters, 'gnomon_biotype')
        run_gnomon_biotype(models_files, splices_files, denylist, gencoll_asn, swiss_prot_asn,  lds2_source, raw_blastp_hits, default_params)
    emit:
        biotypes = run_gnomon_biotype.out.biotypes
        prots_rpt = run_gnomon_biotype.out.prots_rpt
        all = run_gnomon_biotype.out.all
}



process run_gnomon_biotype {
    input:
        path models_files
        path splices_files 
        path denylist 
        path gencoll_asn 
        path swiss_prot_asn
        path lds2_source, stageAs: 'genome/*'
        path raw_blastp_hits
        val parameters
    output:
        path ('output/biotypes.tsv'), emit: 'biotypes'
        path ('output/prots_rpt.tsv'), emit: 'prots_rpt'
        path ('output/*'), emit: 'all'
    script:
    """
    mkdir -p output
    mkdir -p ./asncache/
    prime_cache -cache ./asncache/ -ifmt asnb-seq-entry  -i ${swiss_prot_asn} -oseq-ids /dev/null -split-sequences
    echo "${raw_blastp_hits.join('\n')}" > raw_blastp_hits.mft
    lds2_indexer -source genome/ -db LDS2 
    merge_blastp_hits -asn-cache ./asncache/ -nogenbank -lds2 LDS2 -input-manifest raw_blastp_hits.mft -o prot_hits.asn
    echo "${models_files.join('\n')}" > models.mft
    echo "prot_hits.asn" > prot_hits.mft
    echo "${splices_files.join('\n')}" > splices.mft
    if [ -z "$denylist" ]
    then
      gnomon_biotype  -gc $gencoll_asn -asn-cache ./asncache/  -nogenbank -gnomon_models models.mft -o output/biotypes.tsv -o_prots_rpt output/prots_rpt.tsv -prot_hits prot_hits.mft -prot_splices splices.mft  -reftrack-server 'NONE' -allow_lt631 true
    else
      gnomon_biotype  -gc $gencoll_asn -asn-cache ./asncache/  -nogenbank -gnomon_models models.mft -o output/biotypes.tsv -o_prots_rpt output/prots_rpt.tsv -prot_denylist $denylist -prot_hits prot_hits.mft -prot_splices splices.mft  -reftrack-server 'NONE' -allow_lt631 true
    fi
    """
    stub:
    """
    mkdir -p output
    touch output/prots_rpt.tsv
    touch output/biotypes.tsv
    """
}


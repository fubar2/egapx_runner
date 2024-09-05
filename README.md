# Galaxy tool wrapping the Eukaryotic Genome Annotation Pipeline - External (EGAPx) 

### Please, do not use it for production. 
It was written as a proof of concept, so might be a useful resource for other developers

The production version developed from this prototype is here: https://github.com/richard-burhans/galaxytools/tree/main/tools/ncbi_egapx

![image](https://github.com/user-attachments/assets/65ef814f-dfaa-4698-bbd0-dc8882555a98)

#### Sample VGP TreeValGal JBrowse2 tracks for bColStr4. Coverage at the top, then 4 NCBI annotation tracks with the EGAPx gff track at bottom.
*Note that the track above is the very, very similar looking NCBI protein track for this bird. It was generated from the same RNA-seq data in SRA, 
using the existing main NCBI software that EGAPx represents. That similarity seems to confirm that the new workflow and the tool wrapping it work well. 
The tracks are not identical, but most differences are small. Not bad considering it's an alpha release...*


## Notes about the tool
This is a very simple and crude way to run the EGAPx workflow inside Galaxy.

EGAPx requires huge resources to run with useful data. 128GB and 32 cores are the minimum; 256GB and 64 cores are recommended.

There is a special test minimal example that can be run in 6GB with 4 cores.

The user must supply a yaml configuration file in this initial proof of concept.
Samples are available in the EGAPx github repository and one is shown below for cut/paste into a history dataset in the upload tool.

This is not intended for production. Just a proof of concept.
It is possibly too inefficient to be useful although it may turn out not to be a problem if run on a dedicated workstation.
At least the efficiency can now be more easily estimated.

This is not recommended for public deployment because of the resource demands.

Note that this tool contains a clone of the egapx workflow repository because that's what it needs to run.


## Notes from the egapx documentation

> [!NOTE]
> Copied from the [EGAPx github](https://github.com/ncbi/egapx/) documentation

EGAPx is the publicly accessible version of the updated NCBI [Eukaryotic Genome Annotation Pipeline](https://www.ncbi.nlm.nih.gov/genome/annotation_euk/process/). 

EGAPx takes an assembly fasta file, a taxid of the organism, and RNA-seq data. Based on the taxid, EGAPx will pick protein sets and HMM models. The pipeline runs `miniprot` to align protein sequences, and `STAR` to align RNA-seq to the assembly. Protein alignments and RNA-seq read alignments are then passed to `Gnomon` for gene prediction. In the first step of `Gnomon`, the short alignments are chained together into putative gene models. In the second step, these predictions are further supplemented by _ab-initio_ predictions based on HMM models. The final annotation for the input assembly is produced as a `gff` file. 

The current version is an alpha release with limited features and organism scope to collect initial feedback on execution. Outputs are not yet complete and not intended for production use. Please open a GitHub [Issue](https://github.com/ncbi/egapx/issues)  if you encounter any problems with EGAPx. You can also write to cgr@nlm.nih.gov to give us your feedback or if you have any questions.  


**Security Notice:**
EGAPx has dependencies in and outside of its execution path that include several thousand files from the [NCBI C++ toolkit](https://www.ncbi.nlm.nih.gov/toolkit), and more than a million total lines of code. Static Application Security Testing has shown a small number of verified buffer overrun security vulnerabilities. Users should consult with their organizational security team on risk and if there is concern, consider mitigating options like running via VM or cloud instance. 

## Input example
 
- A test example YAML file `./examples/input_D_farinae_small.yaml` is included in the `egapx` folder. Here, the RNA-seq data is provided as paths to the reads FASTA files. These FASTA files are a sampling of the reads from the complete SRA read files to expedite testing. 


  ```
  genome: https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/020/809/275/GCF_020809275.1_ASM2080927v1/GCF_020809275.1_ASM2080927v1_genomic.fna.gz
  taxid: 6954
  reads:
    - https://ftp.ncbi.nlm.nih.gov/genomes/TOOLS/EGAP/data/Dermatophagoides_farinae_small/SRR8506572.1
    - https://ftp.ncbi.nlm.nih.gov/genomes/TOOLS/EGAP/data/Dermatophagoides_farinae_small/SRR8506572.2
    - https://ftp.ncbi.nlm.nih.gov/genomes/TOOLS/EGAP/data/Dermatophagoides_farinae_small/SRR9005248.1
    - https://ftp.ncbi.nlm.nih.gov/genomes/TOOLS/EGAP/data/Dermatophagoides_farinae_small/SRR9005248.2
  ```

- To specify an array of NCBI SRA datasets:
   ```
   reads:
     - SRR8506572
     - SRR9005248
   ```

- To specify an SRA entrez query:
    ```
    reads: 'txid6954[Organism] AND biomol_transcript[properties] NOT SRS024887[Accession] AND (SRR8506572[Accession] OR SRR9005248[Accession] )'
    ```

  **Note:** Both the above examples will have more RNA-seq data than the `input_D_farinae_small.yaml` example. To make sure the entrez query does not produce a large number of SRA runs, please run it first at the [NCBI SRA page](https://www.ncbi.nlm.nih.gov/sra). If there are too many SRA runs, then select a few of them and list it in the input yaml.   

## Output

Look at the output in the out diectory (`example_out`) that was supplied in the command line. The annotation file is called `accept.gff`. 
```
accept.gff
annot_builder_output
nextflow.log
run.report.html
run.timeline.html
run.trace.txt
run_params.yaml
```
The `nextflow.log` is the log file that captures all the process information and their work directories. `run_params.yaml` has all the parameters that were used in the EGAPx run. More information about the process time and resources can be found in the other run* files.  



## Intermediate files

In the above log, each line denotes the process that completed in the workflow. The first column (_e.g._ `[96/621c4b]`) is the subdirectory where the intermediate output files and logs are found for the process in the same line, _i.e._, `egapx:miniprot:run_miniprot`. To see the intermediate files for that process, you can go to the work directory path that you had supplied and traverse to the subdirectory `96/621c4b`: 

```
$ aws s3 ls s3://temp_datapath/D_farinae/96/      
                           PRE 06834b76c8d7ceb8c97d2ccf75cda4/
                           PRE 621c4ba4e6e87a4d869c696fe50034/
$ aws s3 ls s3://temp_datapath/D_farinae/96/621c4ba4e6e87a4d869c696fe50034/
                           PRE output/
2024-03-27 11:19:18          0 
2024-03-27 11:19:28          6 .command.begin
2024-03-27 11:20:24        762 .command.err
2024-03-27 11:20:26        762 .command.log
2024-03-27 11:20:23          0 .command.out
2024-03-27 11:19:18      13103 .command.run
2024-03-27 11:19:18        129 .command.sh
2024-03-27 11:20:24        276 .command.trace
2024-03-27 11:20:25          1 .exitcode
$ aws s3 ls s3://temp_datapath/D_farinae/96/621c4ba4e6e87a4d869c696fe50034/output/
2024-03-27 11:20:24   17127134 aligns.paf
```


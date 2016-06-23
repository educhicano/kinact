import os
import numpy as np
from pandas import read_csv, pivot_table
import ksea

__all__ = ['ksea']


def id_conversion(lst, fr='uniprot', to='gene_name'):
    """
        Convert UniProt IDs to gene names (or the other way around),
            using a reduced version of the UniProt ID mapping table

        :param lst: List - List of identifiers, either UniProt IDs or Gene names or STRING-IDs
        :param fr: String - String indicating from which system the provided IDs are coming
        :param to: String - String indicating to which system should be matched;
                            For both parameters, combinations of 'uniprot', 'gene_name', or 'string_id' are allowed
        :return: List - List of converted identifiers
    """

    # Check for parameter consistency
    if fr == to:
        raise StandardError("Your function call doesn't make any sense: No need to match from %s to %s" % (fr, to))
    if len({fr, to}.intersection(['uniprot', 'gene_name', 'string_id'])) != 2:
        raise StandardError("Please provide only identifiers of supported systems!")

    # Load reduced id_mapping table from Uniprot
    id_mapping = read_csv(os.path.split(__file__)[0] + '/data/id_conversion.txt')

    # Map ids
    results = [id_mapping.ix[np.where(id_mapping[fr] == s)[0], to].values.tolist()[0]
               if len(id_mapping.ix[np.where(id_mapping[fr] == s)[0], to].values.tolist()) == 1 else np.nan
               for s in lst]
    return results


def get_kinase_targets(sources=None):
    """
        Retrieve kinase-substrate interactions from the OmniPath resource.
        :param sources: [String] - List of sources from which to import kinase-substrate interactions;
                                possible sources include: ['HPRD', 'Li2012', 'MIMP', 'PhosphoNetworks',
                                'PhosphoSite', 'Signor', 'dbPTM', 'phosphoELM', 'DEPOD']

        :return: DataFrame - 1 for reported interaction (-1 for phosphatases), NaN for no interaction
                                    columns: kinase/phosphatase, rows: phospho-sites
    """

    # Check that only allowed sources are used
    if sources is None:
        sources = ['PhosphoSite']
    allowed_sources = ['HPRD', 'Li2012', 'MIMP', 'PhosphoNetworks', 'PhosphoSite',
                       'Signor', 'dbPTM', 'phosphoELM', 'DEPOD']
    if len(set(sources).difference(allowed_sources)) > 0 and sources != ['all']:
        raise StandardError('Please use only the sources integrated into pypath!')
    if sources == ['all']:
        sources = allowed_sources
    # Read in the data from OmniPath
    ptms_omnipath = read_csv(os.path.split(__file__)[0]+'/data/omnipath_ptms.txt', sep='\t')

    # Create unique identifier for each p-site
    ptms_omnipath['p_site'] = ptms_omnipath['UniProt_B'] + \
        '_' + \
        ptms_omnipath['Residue_letter'] + \
        ptms_omnipath['Residue_number'].astype(str)

    # Restrict the data on phosphorylation and dephosphorylation modification
    ptms_omnipath_phospho = ptms_omnipath.where(ptms_omnipath['PTM_type'].str.contains('phosphorylation')).dropna()

    # Add value column
    ptms_omnipath_phospho['value'] = 1

    if len(sources) != len(allowed_sources):
        # Include only interactions of specified sources
        ptms_omnipath_phospho = ptms_omnipath_phospho[[len(set(s.split(';')).intersection(sources)) >= 1
                                                       for s in ptms_omnipath_phospho['Databases']]]

    # Change value to negative value for dephosphorylation events
    ptms_omnipath_phospho.ix[ptms_omnipath_phospho['PTM_type'].str.startswith('de'), 'value'] = -1

    # Create pivot-table from pandas function
    adjacency_matrix = pivot_table(ptms_omnipath_phospho, values='value', index='p_site', columns='UniProt_A')

    # rename columns
    adjacency_matrix.columns = id_conversion(adjacency_matrix.columns.tolist())

    return adjacency_matrix


def prepare_networkin_files(phospho_sites, output_dir=os.getcwd() + '/networkin_files/'):
    """
        Prepare networkin prediction of up-stream kinases for a list of phospho-sites

        :param phospho_sites: List - List of phospho-site identifiers
        :param output_dir: Path - Directory in which to save the files that networkin needs

        :return: None - does not return anything, but saves the relevant files for the networkin analysis
    """
    # create output directory if it does not exist already
    if not os.path.exists(output_dir):
        os.mkdir(output_dir)

    # load UniProt/SwissProt sequences from the supplied data
    sequences = read_csv(os.path.split(__file__)[0] + '/data/sequences.tab', sep='\t', usecols=[0, 3], index_col=0)

    # open output files
    site_file = open(output_dir + 'site_file.txt', 'w+')
    fasta_file = open(output_dir + 'fasta_file.txt', 'w+')

    # keep track of which sequences have already been written in the fasta file
    added_sequences = list()

    for psite in phospho_sites:
        # enter each phospho-site in the sites-file in the required format for networkin
        site_file.write(psite.split('_')[0] + '\t' + psite.split('_')[1][1:] + '\t' + psite.split('_')[1][0] + '\n')

        # save the sequence of the complete protein into the fasta file, so that networkin can map the phospho-sites
        if not psite.split('_')[0] in added_sequences:
            if psite.split('_')[0] in sequences.index:
                fasta_file.write('>' + psite.split('_')[0] + '\n')
                fasta_file.write(sequences.ix[psite.split('_')[0]].values[0] + '\n')
                added_sequences.append(psite.split('_')[0])
            else:
                added_sequences.append(psite.split('_')[0])
    # Close files and print success message
    site_file.close()
    fasta_file.close()
    print 'Files for NetworKIN analysis successfully saved in %s' % output_dir


def get_kinase_targets_from_networkin(file_path, add_omnipath=True, binarize=True, score_cut_off=0.5):
    """
        Convert output of networkin to adjacency matrix

        :param file_path: Path - Path to the networkin output/result file
        :param add_omnipath: Boolean - Indicates whether to add the curated information from omnipath
        :param binarize: Boolean - Indicates whether the matrix should consist of only 1 and 0, or of normalised scores
        :param score_cut_off: Float - cut off for the networkin score

        :return: DataFrame - interactions between kinases/phosphatases and individual p-sites are assigned
                                    with their respective score from networkin
    """
    # read results file generate by networkin (release 3.0)
    nwkin_results = read_csv(file_path, sep='\t')

    # re-create unique phospho-site identifiers
    nwkin_results['p_site'] = nwkin_results['#Name'] + '_' + nwkin_results['Position']

    # restrict output to kinases and phosphatases
    nwkin_results = nwkin_results.where((nwkin_results['Tree'] == 'KIN') | (nwkin_results['Tree'] == 'PTP')).dropna()

    # Save phosphatases
    ptp = np.unique(nwkin_results['Kinase/Phosphatase/Phospho-binding domain description'].where(
        nwkin_results['Tree'] == 'PTP').dropna())

    # Create adjacency_matrix
    adjacency_matrix = pivot_table(data=nwkin_results,
                                   values='NetworKIN score',
                                   index='p_site',
                                   columns='Kinase/Phosphatase/Phospho-binding domain description')

    # Set all scores below the cut-off to NA
    adjacency_matrix = adjacency_matrix.where(adjacency_matrix > score_cut_off, np.nan)

    # Binarize or normalize adjacency matrix
    if binarize:
        adjacency_matrix[adjacency_matrix > score_cut_off] = 1
        adjacency_matrix[adjacency_matrix < score_cut_off] = np.nan
    else:
        # Normalise networKIN scores
        adjacency_matrix = adjacency_matrix.divide(adjacency_matrix.max(axis=1), axis=0)

    # Add omnipath/pypath resources
    if add_omnipath:
        omnipath = get_kinase_targets(sources=['all'])

        adjacency_matrix = adjacency_matrix.reindex(columns=list(set(omnipath.columns.tolist()).union(
            adjacency_matrix.columns.tolist())))

        # Add information from curated data
        for p_site in list(set(omnipath.index).intersection(adjacency_matrix.index)):
            for kin in omnipath.loc[p_site, :].replace(0, np.nan).dropna().index:
                if omnipath.ix[p_site, kin] == -1:
                    adjacency_matrix.ix[p_site, kin] = -1
                else:
                    adjacency_matrix.ix[p_site, kin] = 1

        # Convert scores for phosphatases
        adjacency_matrix[ptp] = - adjacency_matrix[ptp]

        return adjacency_matrix
    else:

        # Convert scores for phosphatases
        adjacency_matrix[ptp] = - adjacency_matrix[ptp]

        return adjacency_matrix


def _update_pypath_resource():
    """
        Update the file 'omnipath_ptms.txt',
        which is used to save the prior knowledge about kinase-substrate interactions from the Omnipath/pypath resource

        Use with care! Additionally, pypath must by installed on your machine.

        :return: None
    """
    # try importing pypath
    try:
        from pypath import main
    except:
        raise StandardError('The package pypath is not installed on your machine. '
                            'Please install pypath before you continue!')

    pa = main.PyPath()

    pa.init_network()

    pa.load_ptms()

    pa.export_ptms_tab(outfile=os.path.split(__file__)[0]+'/data/omnipath_ptms.txt')

    print 'pypath resource successfully updated!'

# TODO: function to update the uniprot id mapping table


def get_example_data():
    """
        Read in the dataset from the publication from de Graaf et al. in MCP,
        and process it in order to use it for the other functions in kinact.

        :return: Tupel - (data_fc - fold change data organised as pandas DataFrame
                                    with different time points as columns and phosphosites as rows
                          data_p_value - p-values organised as data_fc, transformed as neg. logarithm with base 10
                          )
    """
    # Read data
    data_raw = read_csv(os.path.split(__file__)[0] + '/data/deGraaf_2014_jurkat.csv', sep=',', header=0)

    # Filter for those p-sites that were matched ambiguously
    data_reduced = data_raw[~data_raw['Proteins'].str.contains(';')]

    # Create identifier for each phosphorylation site, e.g. P06239_S59 for the Serine 59 in the protein Lck
    data_reduced.loc[:, 'ID'] = data_reduced['Proteins'] + '_' + data_reduced['Amino acid'] + \
        data_reduced['Positions within proteins']
    data_indexed = data_reduced.set_index('ID')

    # Extract only relevant columns
    data_relevant = data_indexed[[x for x in data_indexed if x.startswith('Average')]]

    # Rename columns
    data_relevant.columns = [x.split()[-1] for x in data_relevant]

    # Convert abundances into fold changes compared to control (0 minutes after stimulation)
    data_fc = data_relevant.sub(data_relevant['0min'], axis=0)
    data_fc.drop('0min', axis=1, inplace=True)

    # Also extract the p-values for the fold changes
    data_p_value = data_indexed[[x for x in data_indexed if x.startswith('p value') and x.endswith('vs0min')]]
    data_p_value.columns = [x.split('_')[-1].split('vs')[0] + 'min' for x in data_p_value]
    data_p_value = data_p_value.astype('float')  # Excel saved the p-values as strings, not as floating point numbers

    return data_fc, data_p_value

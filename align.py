#!/usr/bin/env python

""" Command-line usage:
      python align.py [options] wave_file transcript_file output_file
      where options may include:
        -r sampling_rate -- override which sample rate model to use, one of 8000, 11025, and 16000
        -s start_time    -- start of portion of wavfile to align (in seconds, default 0)
        -e end_time      -- end of portion of wavfile to align (in seconds, defaul to end)
            
    You can also import this file as a module and use the functions directly.
"""

import os
import shutil
import wave
import re

try:
    import simplejson as json
except:
    import json

# for converting numbers to words
import inflect
import jsonschema
import click
import tgt

# this may only work when this is run from the command line
this_dir = os.path.dirname(os.path.realpath(__file__))

TRANSCRIPT_SCHEMA = json.load(open(os.path.join(this_dir, "alignment-schemas/transcript_schema.json")))
ALIGNMENT_SCHEMA = json.load(open(os.path.join(this_dir, "alignment-schemas/alignment_schema.json")))

from .pronunciation import Pronounce

# global that maps the original punctuation to the output all-caps
# with stripped punctuation
global_word_map = []
global_speaker_map = []
global_emo_map = []
global_lineidx_map = []


def prep_wav(orig_wav, out_wav, sr_override, sr_models, wave_start, wave_end):
    if os.path.exists(out_wav) and False:
        f = wave.open(out_wav, 'r')
        SR = f.getframerate()
        f.close()
        print("Already re-sampled the wav file to " + str(SR))
        return SR

    f = wave.open(orig_wav, 'r')
    SR = f.getframerate()
    f.close()

    soxopts = ""
    if float(wave_start) != 0.0 or wave_end != None:
        soxopts += " trim " + wave_start
        if wave_end != None:
            soxopts += " " + str(float(wave_end) - float(wave_start))

    if (sr_models != None and SR not in sr_models) or (sr_override != None and SR != sr_override) or soxopts != "":
        new_sr = 11025
        if sr_override != None:
            new_sr = sr_override

        print("Resampling wav file from " + str(SR) + " to " + str(new_sr) + soxopts + "...")
        SR = new_sr
        print("sox " + orig_wav + " -r " + str(SR) + " " + out_wav + "" + soxopts)
        os.system("sox " + orig_wav + " -r " + str(SR) + " " + out_wav + "" + soxopts)
    else:
        # os.system("cp -f " + orig_wav + " " + out_wav)
        shutil.copy(orig_wav, out_wav)

    return SR


def prep_mlf(trsfile, mlffile, word_dictionary, surround, between, dialog_file = False):
    dict_tmp = {}

    infl = inflect.engine()

    # Read in the dictionary to ensure all of the words
    # we put in the MLF file are in the dictionary. Words
    # that are not are skipped with a warning.
    f = open(word_dictionary, 'r')
    dictionary = {}  # build hash table
    for line in f.readlines():
        if line != "\n" and line != "":
            dictionary[line.split()[0]] = True
    f.close()

    speakers = None
    emotions = None

    if dialog_file:
        dialog = json.load(open(trsfile, 'r'))

        # make sure this is a valid transcript
        try:
            jsonschema.validate(dialog, TRANSCRIPT_SCHEMA)
        except jsonschema.ValidationError as e:
            print("Input transcript file is not in the proper format.\nSee alignment-schemas/transcript_schema.json "
                  "or https://github.com/srubin/p2fa-steve")
            raise e

        lines = [dl["line"] for dl in dialog]
        speakers = [dl["speaker"] for dl in dialog]
        if "emotion" in dialog[0]:
            emotions = [dl["emotion"] for dl in dialog]
    else:
        f = open(trsfile, 'r')
        lines = f.readlines()
        f.close()

    words = []

    if surround != None:
        words += surround.split(',')

    i = 0

    # this pattern matches hyphenated words, such as TWENTY-TWO; however, it doesn't work with longer things like SOMETHING-OR-OTHER
    hyphenPat = re.compile(r'([a-zA-Z]+)-([a-zA-Z]+)')

    while (i < len(lines)):
        txt = lines[i].replace('\n', '')
        txt = txt.replace('{br}', '{BR}').replace('&lt;noise&gt;', '{NS}')
        txt = txt.replace('{laugh}', '{LG}').replace('{laughter}', '{LG}')
        txt = txt.replace('{cough}', '{CG}').replace('{lipsmack}', '{LS}')

        for pun in [',', '.', ':', ';', '!', '?', '"', '%', '(', ')', '-', '--', '---']:
            if txt.startswith(pun + ' '):
                txt = txt[2:]

            # remove hanging punctuation before we get started
            txt = txt.replace(' ' + pun + ' ', ' ')

        hyph_punct = re.compile(r"(-[-]+[,\.:;!\?\"%\(\)-]*)")
        txt = hyph_punct.sub(r"\1 ", txt)

        txt = re.sub(r"([A-Za-z])\.\.\.([A-Za-z])", r"\1... \2", txt)

        txt_with_pun = txt.split()

        for pun in ['...']:
            txt = txt.replace(pun, '')

        for pun in [',', '.', ':', ';', '!', '?', '"', '%', '(', ')', '--', '---']:
            txt = txt.replace(pun, '')

        txt = re.sub('\s+', ' ', txt)
        txt = re.sub(r"\s'", " ", txt)

        txt = txt.split()

        if (len(txt) != len(txt_with_pun)):
            # Try not to use hyphenated words either, if at all possible!
            import pdb;
            pdb.set_trace()
            raise Exception("Floating punctuation! Remove this from your transcript.")

        for w_idx, wrd in enumerate(txt):
            # break up any hyphenated words into two separate words
            new_wrd = re.sub(hyphenPat, r'\1 \2', wrd)
            new_wrd = new_wrd.split()

            gwm_entry = [txt_with_pun[w_idx]]

            new_up_wrd = [x.upper() for x in new_wrd]
            # print new_wrd
            # print new_up_wrd
            for wrd2 in new_up_wrd:
                if (wrd2 not in dictionary) and (wrd2 not in dict_tmp):
                    print(wrd2)
                    try:
                        if wrd2[-1] in ['s', 'S']:
                            twrd2 = int(wrd2[:-1])
                        else:
                            twrd2 = int(wrd2)
                        num2wrd = infl.number_to_words(twrd2, andword = '', threshold = 1000)

                        if len(str(twrd2)) == 4 and 1000 < twrd2 < 2000:
                            # this is probably a year
                            year1 = str(twrd2)[:2]
                            year2 = str(twrd2)[2:]

                            year1word = infl.number_to_words(int(year1), andword = '', threshold = 1000)
                            year2word = infl.number_to_words(int(year2), andword = '', threshold = 1000)

                            extraword = None
                            if year2 == "00":
                                year2word = "HUNDRED"
                            elif year2[0] == "0":
                                extraword = "OH"
                                year2word = infl.number_to_words(int(year2[1]), andword = '', threshold = 1000)

                            year_all_words = [year1word, year2word]
                            if extraword is not None:
                                year_all_words.append(extraword)

                            yearprs = Pronounce(words = year_all_words).p(add_fake_stress = True)
                            year1pr = yearprs[year1word][1]
                            year2pr = yearprs[year2word][1]

                            if extraword is not None:
                                dict_tmp[wrd2] = year1pr + ' ' + yearprs[extraword][1] + ' ' + year2pr
                            else:
                                dict_tmp[wrd2] = year1pr + ' ' + year2pr
                            print(wrd2, dict_tmp[wrd2])

                        else:
                            num2wrd = num2wrd.upper()
                            num2wrd = num2wrd.replace('-', ' ')
                            num2wrd = num2wrd.replace(',', '')
                            num2wrd = num2wrd.replace(' ', '')
                            print(num2wrd)
                            # import pdb; pdb.set_trace()
                            pr = Pronounce(words = [num2wrd]).p(add_fake_stress = True)
                            prn = pr[num2wrd][1]
                            if wrd2[-1] in ['s', 'S']:
                                prn += ' S'
                            dict_tmp[wrd2] = prn
                            print(prn)
                    except:
                        # print "###", e
                        pr = Pronounce(words = [wrd2]).p(add_fake_stress = True)
                        dict_tmp[pr[wrd2][0]] = pr[wrd2][1]
                        print(pr)

                words.append(wrd2)
                gwm_entry.append(wrd2)
                if len(between) != 0:
                    words.extend(between)

                    # try:  #     int(wrd2)  #     numwrds = infl.number_to_words(wrd2, andword='')  #     numwrds = numwrds.upper()  #     numwrds = numwrds.replace('-', ' ')  #     numwrds = numwrds.replace(',', '')  #     numwrds = numwrds.split()  #       #     for w in numwrds:  #         if w in dictionary:  #             words.append(w)  #             gwm_entry.append(w)  #             if between != None:  #                 words.append(between)  #         else:  #             print "SKIPPING NUM WORD", w  # except Exception, e:  #     print e  #     print "SKIPPING WORD", wrd2
            if len(gwm_entry) > 1:
                global_word_map.append(gwm_entry)
                global_lineidx_map.append(i)
                if speakers is not None:
                    global_speaker_map.append(speakers[i])
                if emotions is not None:
                    global_emo_map.append(emotions[i])
        i += 1

    # remove the last 'between' token from the end
    if between != None:
        words = words[:-len(between)]

    if surround != None:
        words += surround.split(',')

    writeInputMLF(mlffile, words)
    writeDictTmp(dict_tmp)


def writeInputMLF(mlffile, words):
    fw = open(mlffile, 'w')
    fw.write('#!MLF!#\n')
    fw.write('"*/tmp.lab"\n')
    for wrd in words:
        if wrd.startswith("'"):
            wrd = "\\" + wrd
        try:
            int(wrd[0])
            wrd = '"' + wrd + '"'
        # wrd = re.sub(r"(\d)", r"\\\1", wrd)
        except:
            pass
        # print wrd
        fw.write(wrd + '\n')
    fw.write('.\n')
    fw.close()


def writeDictTmp(dict_tmp):
    if len(dict_tmp.keys()) > 0:
        with open("dict.tmp", 'w') as f:
            for w, pr in dict_tmp.items():
                f.write("%s  %s\n" % (w, pr))


def readAlignedMLF(mlffile, SR, wave_start):
    # This reads a MLFalignment output  file with phone and word
    # alignments and returns a list of words, each word is a list containing
    # the word label followed by the phones, each phone is a tuple
    # (phone, start_time, end_time) with times in seconds.

    f = open(mlffile, 'r')
    lines = [l.rstrip() for l in f.readlines()]
    f.close()

    if len(lines) < 3:
        raise ValueError("Alignment did not complete succesfully.")

    j = 2
    ret = []
    while (lines[j] != '.'):
        if (len(lines[j].split()) == 5):  # Is this the start of a word; do we have a word label?
            # Make a new word list in ret and put the word label at the beginning
            wrd = lines[j].split()[4]
            ret.append([wrd])

        # Append this phone to the latest word (sub-)list
        ph = lines[j].split()[2]
        if (SR == 11025):
            st = (float(lines[j].split()[0]) / 10000000.0 + 0.0125) * (11000.0 / 11025.0)
            en = (float(lines[j].split()[1]) / 10000000.0 + 0.0125) * (11000.0 / 11025.0)
        else:
            st = float(lines[j].split()[0]) / 10000000.0 + 0.0125
            en = float(lines[j].split()[1]) / 10000000.0 + 0.0125
        if st < en:
            ret[-1].append([ph, st + wave_start, en + wave_start])

        j += 1

    return ret


# steve added 1/23/2013
def writeJSON(outfile, word_alignments, phonemes = False):
    # make the list of just phone alignments
    phons = []
    word_phons = []
    for wrd in word_alignments:
        phons.extend(wrd[1:])  # skip the word label
        if len(wrd) != 1:
            word_phons.append(wrd[1:])

    # make the list of just word alignments
    # we're getting elements of the form:
    #   ["word label", ["phone1", start, end], ["phone2", start, end], ...]

    wrds = []
    for wrd in word_alignments:
        # If no phones make up this word, then it was an optional word
        # like a pause that wasn't actually realized.
        if len(wrd) == 1:
            continue
        wrds.append([wrd[0], wrd[1][1], wrd[-1][2]])  # word label, first phone start time, las t phone end time

    out_dict = {"words": []}

    real_word_count = 0
    total_word_idx = 0

    while total_word_idx < len(wrds) - 1:
        # if wrds[k][0] == "sp":
        #     continue

        print(wrds[total_word_idx], global_word_map[real_word_count])

        if wrds[total_word_idx][0] != "sp" and wrds[total_word_idx][0] != "{BR}":
            word_length = len(global_word_map[real_word_count]) - 1
        else:
            word_length = 1

        try:

            tmp_word = {"alignedWord": wrds[total_word_idx][0], "start": round(wrds[total_word_idx][1], 5),
                        "end": round(wrds[total_word_idx + word_length - 1][2], 5)
                        # "end": round(wrds[total_word_idx + word_length][1], 5)
                        }
        except:
            import pdb;
            pdb.set_trace()

        if wrds[total_word_idx][0] != "sp" and wrds[total_word_idx][0] != "{BR}":
            tmp_word["word"] = global_word_map[real_word_count][0]
            if phonemes:
                tmp_word["phonemes"] = []
                for wl_i in range(word_length):
                    tmp_word["phonemes"].extend(word_phons[total_word_idx + wl_i])

            tmp_word["line_idx"] = global_lineidx_map[real_word_count]

            if len(global_speaker_map) > 0:
                tmp_word["speaker"] = global_speaker_map[real_word_count]
            if len(global_emo_map) > 0:
                tmp_word["emotion"] = global_emo_map[real_word_count]

            real_word_count += 1
        elif wrds[total_word_idx][0] == "sp":
            tmp_word["word"] = "{p}"
        elif wrds[total_word_idx][0] == "{BR}":
            tmp_word["word"] = "{br}"

        # if word_length > 1:
        #     import pdb; pdb.set_trace()

        if word_length == 1:
            total_word_idx += 1
        else:
            skipped_pauses = 0
            real_words_to_skip = word_length - 1
            while total_word_idx < len(wrds) and real_words_to_skip > 0:
                total_word_idx += 1
                if wrds[total_word_idx][0] != "sp" and wrds[total_word_idx][0] != "{BR}":
                    real_words_to_skip -= 1
                else:
                    skipped_pauses += 1
            total_word_idx += 1
            tmp_word["end"] = round(wrds[total_word_idx - 1][2], 5)
            # tmp_word["end"] = round(wrds[total_word_idx][1], 5)
            tmp_word["alignedWord"] = " ".join(
                [w[0] for w in wrds[total_word_idx - skipped_pauses - word_length: total_word_idx]])

        # real_words_to_skip = word_length
        # total_word_idx += 1
        # while total_word_idx < len(wrds) - 1 and real_words_to_skip > 0:
        #     if wrds[total_word_idx][0] != "sp":
        #         real_words_to_skip -= 1
        #     total_word_idx += 1

        out_dict["words"].append(tmp_word)

    tmp_word = {"alignedWord": wrds[-1][0], "start": round(wrds[-1][1], 5), "end": round(phons[-1][2], 5)}

    dont_add = False

    if wrds[-1][0] != "sp" and wrds[-1][0] != "{BR}":
        try:
            tmp_word["word"] = global_word_map[real_word_count][0]
            tmp_word["line_idx"] = global_lineidx_map[real_word_count]

            if len(global_speaker_map) > 0:
                tmp_word["speaker"] = global_speaker_map[real_word_count]
            if len(global_emo_map) > 0:
                tmp_word["emotion"] = global_emo_map[real_word_count]

            if phonemes:
                tmp_word["phonemes"] = word_phons[total_word_idx]

        except:
            # will get here if last word is compound word
            dont_add = True
            pass

    elif wrds[-1][0] == "sp":
        tmp_word["word"] = "{p}"
    elif wrds[-1][0] == "{BR}":
        tmp_word["word"] = "{br}"

    if not dont_add:
        out_dict["words"].append(tmp_word)

    try:
        jsonschema.validate(out_dict, ALIGNMENT_SCHEMA)
    except jsonschema.ValidationError as e:
        print("Output is not a valid Alignment according to alignment-schemas/alignment_schema.json")
        print(e)

    with open(outfile, "w") as f_out:
        json.dump(out_dict, f_out, indent = 4)


def writeTextGrid(outfile, word_alignments):
    tg = tgt.TextGrid()
    phone_tier = tgt.IntervalTier(name = 'phone')
    word_tier = tgt.IntervalTier(name = 'word')

    for data in word_alignments:
        word = data[0]
        phones = data[1:]

        if len(phones) > 0:
            start_time = phones[0][1]
            end_time = phones[-1][2]

            word_tier.add_interval(tgt.Interval(start_time, end_time, text = word))

            for (p, p_start, p_end) in phones:
                phone_tier.add_interval(tgt.Interval(p_start, p_end, text = p))
    tg.add_tier(phone_tier)
    tg.add_tier(word_tier)

    tgt.io.write_to_file(tg, outfile, format = 'long')

    # # make the list of just phone alignments  # phons = []  # for wrd in word_alignments :  #     phons.extend(wrd[1:]) # skip the word label

    # # make the list of just word alignments  # # we're getting elements of the form:  # #   ["word label", ["phone1", start, end], ["phone2", start, end], ...]  # wrds = []  # for wrd in word_alignments :  #     # If no phones make up this word, then it was an optional word  #     # like a pause that wasn't actually realized.  #     if len(wrd) == 1 :  #         continue  #     wrds.append([wrd[0], wrd[1][1], wrd[-1][2]]) # word label, first phone start time, last phone end time

    # #write the phone interval tier

    # # steve edits 1/23/2013  # fw = open(outfile, 'w')  # # fw.write('File type = "ooTextFile short"\n')  # fw.write('File type = "ooTextFile"\n')  # # fw.write('"TextGrid"\n')  # fw.write('Object class = "TextGrid"\n')  # fw.write('\n')  # fw.write(str(phons[0][1]) + '\n')  # fw.write(str(phons[-1][2]) + '\n')  # fw.write('<exists>\n')  # fw.write('2\n')  # fw.write('"IntervalTier"\n')  # fw.write('"phone"\n')  # fw.write(str(phons[0][1]) + '\n')  # fw.write(str(phons[-1][-1]) + '\n')  # fw.write(str(len(phons)) + '\n')  # for k in range(len(phons)):  #     fw.write(str(phons[k][1]) + '\n')  #     fw.write(str(phons[k][2]) + '\n')  #     fw.write('"' + phons[k][0] + '"' + '\n')

    # #write the word interval tier  # fw.write('"IntervalTier"\n')  # fw.write('"word"\n')  # fw.write(str(phons[0][1]) + '\n')  # fw.write(str(phons[-1][-1]) + '\n')  # fw.write(str(len(wrds)) + '\n')  # for k in range(len(wrds) - 1):  #     fw.write(str(wrds[k][1]) + '\n')  #     fw.write(str(wrds[k+1][1]) + '\n')  #     fw.write('"' + wrds[k][0] + '"' + '\n')

    # fw.write(str(wrds[-1][1]) + '\n')  # fw.write(str(phons[-1][2]) + '\n')  # fw.write('"' + wrds[-1][0] + '"' + '\n')

    # fw.close()


def prep_working_directory():
    if os.path.exists('tmp'):
        shutil.rmtree('tmp')
    os.mkdir('tmp')

    # os.system("rm -r -f ./tmp")  # os.system("mkdir ./tmp")


def prep_scp(wavfile):
    fw = open('tmp/codetr.scp', 'w')
    fw.write(wavfile + ' tmp/tmp.plp\n')
    fw.close()
    fw = open('tmp/test.scp', 'w')
    fw.write('tmp/tmp.plp\n')
    fw.close()


def create_plp(hcopy_config):
    os.system('HCopy -T 1 -C ' + hcopy_config + ' -S tmp/codetr.scp')


def viterbi(input_mlf, word_dictionary, output_mlf, phoneset, hmmdir):
    command = 'HVite -T 1 -a -m -I ' + input_mlf + ' -H ' + hmmdir + '/macros -H ' + hmmdir + '/hmmdefs  -S tmp/test.scp -i ' + output_mlf + ' -p 0.0 -s 5.0 ' + word_dictionary + ' ' + phoneset + ' > tmp/aligned.results'
    print(command)
    # command = 'HVite -T 1 -a -m -I ' + input_mlf + ' -H ' + hmmdir + '/macros -H ' + hmmdir + '/hmmdefs  -S ./tmp/test.scp -i ' + output_mlf + ' -p 0.0 -s 5.0 ' + word_dictionary + ' ' + phoneset
    os.system(command)


def getopt2(name, opts, default = None):
    value = [v for n, v in opts if n == name]
    if len(value) == 0:
        return default
    return value[0]


@click.command()
@click.argument('wavfile')
@click.argument('trsfile')
@click.argument('outfile')
@click.option('--json/--no-json', default = True, help = "Export json alignment")
@click.option('--textgrid/--no-textgrid', default = False, help = "Export Praat TextGrid alignment")
@click.option('--phonemes/--no-phonemes', default = False, help = "Add phoneme information to JSON output")
@click.option('--breaths/--no-breaths', default = False, help = "Detect breaths in speech")
def cli_do_alignment(wavfile, trsfile, outfile, json, textgrid, phonemes, breaths):
    return do_alignment(wavfile, trsfile, outfile, json, textgrid, phonemes, breaths)


def do_alignment(wavfile, trsfile, outfile, json = True, textgrid = False, phonemes = False, breaths = False):
    # sr_override = getopt2("-r", opts, None)
    # wave_start = getopt2("-s", opts, "0.0")
    # wave_end = getopt2("-e", opts, None)
    del global_word_map[:]
    del global_speaker_map[:]
    del global_emo_map[:]
    del global_lineidx_map[:]

    sr_override = None
    wave_start = "0.0"
    wave_end = None
    surround_token = "sp"
    between_token = ["sp"]

    # mypath = getopt2("--model", opts, None)
    mypath = None

    # If no model directory was said explicitly, get directory containing this script.
    hmmsubdir = ""
    sr_models = None
    if mypath == None:
        mypath = os.path.join(os.path.dirname(os.path.realpath(__file__)), "model")
        hmmsubdir = "FROM-SR"
        # sample rates for which there are acoustic models set up, otherwise
        # the signal must be resampled to one of these rates.
        sr_models = [8000, 11025, 16000]

    if sr_override != None and sr_models != None and not sr_override in sr_models:
        raise ValueError("invalid sample rate: not an acoustic model available")

    word_dictionary = "tmp/dict"
    input_mlf = 'tmp/tmp.mlf'
    output_mlf = 'tmp/aligned.mlf'

    # create working directory
    prep_working_directory()

    # create ./tmp/dict by concatening our dict with a local one

    with open(word_dictionary, 'w') as wd_file:
        with open(os.path.join(mypath, 'dict')) as dict_f:
            wd_file.write(dict_f.read())

        if os.path.exists("dict.local"):
            with open("dict.local") as local_dict_f:
                wd_file.write(local_dict_f.read())

    # prepare wavefile: do a resampling if necessary
    tmpwav = "tmp/sound.wav"
    SR = prep_wav(wavfile, tmpwav, sr_override, sr_models, wave_start, wave_end)

    if hmmsubdir == "FROM-SR":
        hmmsubdir = "/" + str(SR)

    # prepare mlfile
    prep_mlf(trsfile, input_mlf, word_dictionary, surround_token, between_token, dialog_file = True)

    # (do this again because we update dict.local in prep_mlf)
    with open(os.path.join(mypath, 'dict')) as dict_f:
        dict_lines = [line for line in dict_f]
    try:
        with open("dict.tmp") as tmp_dict_f:
            dict_lines.extend([line for line in tmp_dict_f])
    except:
        pass
    sorted_dict_lines = sorted(dict_lines)
    with open(word_dictionary, 'w') as wd_file:
        for line in sorted_dict_lines:
            wd_file.write(line)

    # prepare scp files
    prep_scp(tmpwav)

    # generate the plp file using a given configuration file for HCopy
    create_plp(mypath + hmmsubdir + '/config')

    # run Verterbi decoding
    # print "Running HVite..."
    mpfile = mypath + '/monophones'
    if not os.path.exists(mpfile):
        mpfile = mypath + '/hmmnames'
    viterbi(input_mlf, word_dictionary, output_mlf, mpfile, mypath + hmmsubdir)

    if json:
        # output as json
        writeJSON(outfile, readAlignedMLF(output_mlf, SR, float(wave_start)), phonemes = phonemes)

    if textgrid:
        # output the alignment as a Praat TextGrid
        writeTextGrid(outfile, readAlignedMLF(output_mlf, SR, float(wave_start)))


if __name__ == '__main__':
    cli_do_alignment()

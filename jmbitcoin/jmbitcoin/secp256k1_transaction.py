#!/usr/bin/python

# note, only used for non-cryptographic randomness:
import random
import json
# needed for single sha256 evaluation, which is used
# in bitcoin (p2wsh) but not exposed in python-bitcointx:
import hashlib

from jmbitcoin.secp256k1_main import *
from jmbase import bintohex, utxo_to_utxostr
from bitcointx.core import (CMutableTransaction, Hash160, CTxInWitness,
                            CMutableOutPoint, CMutableTxIn, CTransaction,
                            CMutableTxOut, CTxIn, CTxOut, ValidationError)
from bitcointx.core.script import *
from bitcointx.wallet import (P2WPKHCoinAddress, CCoinAddress, P2PKHCoinAddress,
                              CCoinAddressError)
from bitcointx.core.scripteval import (VerifyScript, SCRIPT_VERIFY_WITNESS,
                                       SCRIPT_VERIFY_P2SH, SIGVERSION_WITNESS_V0)

def hrt(tx, jsonified=True):
    """ Given a CTransaction object, output a human
    readable json-formatted string (suitable for terminal
    output or large GUI textbox display) containing
    all details of that transaction.
    If `jsonified` is False, the dict is returned, instead
    of the json string.
    """
    assert isinstance(tx, CTransaction)
    outdict = {}
    outdict["hex"] = bintohex(tx.serialize())
    outdict["inputs"]=[]
    outdict["outputs"]=[]
    outdict["txid"]= bintohex(tx.GetTxid()[::-1])
    outdict["nLockTime"] = tx.nLockTime
    outdict["nVersion"] = tx.nVersion
    for i, inp in enumerate(tx.vin):
        if not tx.wit.vtxinwit:
            # witness section is not initialized/empty
            witarg = None
        else:
            witarg = tx.wit.vtxinwit[i]
        outdict["inputs"].append(hrinp(inp, witarg))
    for i, out in enumerate(tx.vout):
        outdict["outputs"].append(hrout(out))
    if not jsonified:
        return outdict
    return json.dumps(outdict, indent=4)

def hrinp(txinput, txinput_witness):
    """ Pass objects of type CTxIn and CTxInWitness (or None)
    and a dict of human-readable entries for this input
    is returned.
    """
    assert isinstance(txinput, CTxIn)
    outdict = {}
    success, u = utxo_to_utxostr((txinput.prevout.hash[::-1],
                                  txinput.prevout.n))
    assert success
    outdict["outpoint"] = u
    outdict["scriptSig"] = bintohex(txinput.scriptSig)
    outdict["nSequence"] = txinput.nSequence

    if txinput_witness:
        outdict["witness"] = bintohex(
            txinput_witness.scriptWitness.serialize())
    return outdict

def hrout(txoutput):
    """ Returns a dict of human-readable entries
    for this output.
    """
    assert isinstance(txoutput, CTxOut)
    outdict = {}
    outdict["value_sats"] = txoutput.nValue
    outdict["scriptPubKey"] = bintohex(txoutput.scriptPubKey)
    try:
        addr = CCoinAddress.from_scriptPubKey(txoutput.scriptPubKey)
        outdict["address"] = str(addr)
    except CCoinAddressError:
        pass # non standard script
    return outdict

def estimate_tx_size(ins, outs, txtype='p2pkh'):
    '''Estimate transaction size.
    The txtype field as detailed below is used to distinguish
    the type, but there is at least one source of meaningful roughness:
    we assume the output types are the same as the input (to be fair,
    outputs only contribute a little to the overall total). This combined
    with a few bytes variation in signature sizes means we will expect,
    say, 10% inaccuracy here.

    Assuming p2pkh:
    out: 8+1+3+2+20=34, in: 1+32+4+1+1+~73+1+1+33=147,
    ver:4,seq:4, +2 (len in,out)
    total ~= 34*len_out + 147*len_in + 10 (sig sizes vary slightly)
    Assuming p2sh M of N multisig:
    "ins" must contain M, N so ins= (numins, M, N) (crude assuming all same)
    74*M + 34*N + 45 per input, so total ins ~ len_ins * (45+74M+34N)
    so total ~ 34*len_out + (45+74M+34N)*len_in + 10
    Assuming p2sh-p2wpkh:
    witness are roughly 3+~73+33 for each input
    (txid, vin, 4+20 for witness program encoded as scriptsig, 4 for sequence)
    non-witness input fields are roughly 32+4+4+20+4=64, so total becomes
    n_in * 64 + 4(ver) + 4(locktime) + n_out*34
    Assuming p2wpkh native:
    witness as previous case
    non-witness loses the 24 witnessprogram, replaced with 1 zero,
    in the scriptSig, so becomes:
    n_in * 41 + 4(ver) + 4(locktime) +2 (len in, out) + n_out*34
    '''
    if txtype == 'p2pkh':
        return 10 + ins * 147 + 34 * outs
    elif txtype == 'p2sh-p2wpkh':
        #return the estimate for the witness and non-witness
        #portions of the transaction, assuming that all the inputs
        #are of segwit type p2sh-p2wpkh
        # Note as of Jan19: this misses 2 bytes (trivial) for len in, out
        # and also overestimates output size by 2 bytes.
        witness_estimate = ins*109
        non_witness_estimate = 4 + 4 + outs*34 + ins*64
        return (witness_estimate, non_witness_estimate)
    elif txtype == 'p2wpkh':
        witness_estimate = ins*109
        non_witness_estimate = 4 + 4 + 2 + outs*31 + ins*41
        return (witness_estimate, non_witness_estimate)
    elif txtype == 'p2shMofN':
        ins, M, N = ins
        return 10 + (45 + 74*M + 34*N) * ins + 34 * outs
    else:
        raise NotImplementedError("Transaction size estimation not" +
                                  "yet implemented for type: " + txtype)

def pubkey_to_p2pkh_script(pub, require_compressed=False):
    """
    Given a pubkey in bytes, return a CScript
    representing the corresponding pay-to-pubkey-hash
    scriptPubKey.
    """
    return P2PKHCoinAddress.from_pubkey(pub).to_scriptPubKey()

def pubkey_to_p2wpkh_script(pub):
    """
    Given a pubkey in bytes (compressed), return a CScript
    representing the corresponding pay-to-witness-pubkey-hash
    scriptPubKey.
    """
    return P2WPKHCoinAddress.from_pubkey(pub).to_scriptPubKey()

def pubkey_to_p2sh_p2wpkh_script(pub):
    """
    Given a pubkey in bytes, return a CScript representing
    the corresponding nested pay to witness keyhash
    scriptPubKey.
    """
    if not is_valid_pubkey(pub, True):
        raise Exception("Invalid pubkey")
    return pubkey_to_p2wpkh_script(pub).to_p2sh_scriptPubKey()

def redeem_script_to_p2wsh_script(redeem_script):
    """ Given redeem script of type CScript (or bytes)
    returns the corresponding segwit v0 scriptPubKey as
    for the case pay-to-witness-scripthash.
    """
    return standard_witness_v0_scriptpubkey(
        hashlib.sha256(redeem_script).digest())

def mk_freeze_script(pub, locktime):
    """
    Given a pubkey and locktime, create a script which can only be spent
    after the locktime has passed using OP_CHECKLOCKTIMEVERIFY
    """
    if not isinstance(locktime, int):
        raise TypeError("locktime must be int")
    if not isinstance(pub, bytes):
        raise TypeError("pubkey must be in bytes")
    usehex = False
    if not is_valid_pubkey(pub, require_compressed=True):
        raise ValueError("not a valid public key")
    return CScript([locktime, OP_CHECKLOCKTIMEVERIFY, OP_DROP, pub,
                    OP_CHECKSIG])

def mk_burn_script(data):
    """ For a given bytestring (data),
    returns a scriptPubKey which is an OP_RETURN
    of that data.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be in bytes")
    return CScript([btc.OP_RETURN, data])

def sign(tx, i, priv, hashcode=SIGHASH_ALL, amount=None, native=False):
    """
    Given a transaction tx of type CMutableTransaction, an input index i,
    and a raw privkey in bytes, updates the CMutableTransaction to contain
    the newly appended signature.
    Only four scriptPubKey types supported: p2pkh, p2wpkh, p2sh-p2wpkh, p2wsh.
    Note that signing multisig must be done outside this function, using
    the wrapped library.
    If native is not the default (False), and if native != "p2wpkh",
    then native must be a CScript object containing the redeemscript needed to sign.
    Returns: (signature, "signing succeeded")
    or: (None, errormsg) in case of failure
    """
    # script verification flags
    flags = set()

    def return_err(e):
        return None, "Error in signing: " + repr(e)

    assert isinstance(tx, CMutableTransaction)

    pub = privkey_to_pubkey(priv)

    if not amount:
        # p2pkh only supported here:
        input_scriptPubKey = pubkey_to_p2pkh_script(pub)
        sighash = SignatureHash(input_scriptPubKey, tx, i, hashcode)
        try:
            sig = ecdsa_raw_sign(sighash, priv, rawmsg=True) + bytes([hashcode])
        except Exception as e:
            return return_err(e)
        tx.vin[i].scriptSig = CScript([sig, pub])
        # Verify the signature worked.
        try:
            VerifyScript(tx.vin[i].scriptSig,
                        input_scriptPubKey, tx, i, flags=flags)
        except Exception as e:
            return return_err(e)
        return sig, "signing succeeded"

    else:
        # segwit case; we currently support p2wpkh native or under p2sh.

        # https://github.com/Simplexum/python-bitcointx/blob/648ad8f45ff853bf9923c6498bfa0648b3d7bcbd/bitcointx/core/scripteval.py#L1250-L1252
        flags.add(SCRIPT_VERIFY_P2SH)

        if native and native != "p2wpkh":
            scriptCode = native
            input_scriptPubKey = redeem_script_to_p2wsh_script(native)
        else:
            # this covers both p2wpkh and p2sh-p2wpkh case:
            input_scriptPubKey = pubkey_to_p2wpkh_script(pub)
            # only created for convenience access to scriptCode:
            input_address = P2WPKHCoinAddress.from_scriptPubKey(
                input_scriptPubKey)
            # function name is misleading here; redeemScript only applies to p2sh.
            scriptCode = input_address.to_redeemScript()

        sighash = SignatureHash(scriptCode, tx, i, hashcode, amount=amount,
                                sigversion=SIGVERSION_WITNESS_V0)
        try:
            sig = ecdsa_raw_sign(sighash, priv, rawmsg=True) + bytes([hashcode])
        except Exception as e:
            return return_err(e)
        if native:
            flags.add(SCRIPT_VERIFY_WITNESS)
        else:
            tx.vin[i].scriptSig = CScript([input_scriptPubKey])

        if native and native != "p2wpkh":
            witness = [sig, scriptCode]
        else:
            witness = [sig, pub]
        ctxwitness = CTxInWitness(CScriptWitness(witness))
        tx.wit.vtxinwit[i] = ctxwitness
        # Verify the signature worked.
        try:
            VerifyScript(tx.vin[i].scriptSig, input_scriptPubKey, tx, i,
                     flags=flags, amount=amount, witness=tx.wit.vtxinwit[i].scriptWitness)
        except ValidationError as e:
            return return_err(e)

        return sig, "signing succeeded"

def mktx(ins, outs, version=1, locktime=0):
    """ Given a list of input tuples (txid(bytes), n(int)),
    and a list of outputs which are dicts with
    keys "address" (value should be *str* not CCoinAddress) (
    or alternately "script" (for nonstandard outputs, value
    should be CScript)),
    "value" (value should be integer satoshis), outputs a
    CMutableTransaction object.
    Tx version and locktime are optionally set, for non-default
    locktimes, inputs are given nSequence as per below comment.
    """
    vin = []
    vout = []
    # This does NOT trigger rbf and mimics Core's standard behaviour as of
    # Jan 2019.
    # Tx creators wishing to use rbf will need to set it explicitly outside
    # of this function.
    if locktime != 0:
        sequence = 0xffffffff - 1
    else:
        sequence = 0xffffffff
    for i in ins:
        outpoint = CMutableOutPoint((i[0][::-1]), i[1])
        inp = CMutableTxIn(prevout=outpoint, nSequence=sequence)
        vin.append(inp)
    for o in outs:
        if "script" in o:
            sPK = o["script"]
        else:
            # note the to_scriptPubKey method is only available for standard
            # address types
            sPK = CCoinAddress(o["address"]).to_scriptPubKey()
        out = CMutableTxOut(o["value"], sPK)
        vout.append(out)
    return CMutableTransaction(vin, vout, nLockTime=locktime, nVersion=version)

def make_shuffled_tx(ins, outs, version=1, locktime=0):
    """ Simple wrapper to ensure transaction
    inputs and outputs are randomly ordered.
    Can possibly be replaced by BIP69 in future
    """
    random.shuffle(ins)
    random.shuffle(outs)
    return mktx(ins, outs, version=version, locktime=locktime)

def verify_tx_input(tx, i, scriptSig, scriptPubKey, amount=None,
                    witness=None, native=False):
    flags = set()
    if witness:
        flags.add(SCRIPT_VERIFY_P2SH)
    if native:
        flags.add(SCRIPT_VERIFY_WITNESS)
    try:
        VerifyScript(scriptSig, scriptPubKey, tx, i,
                 flags=flags, amount=amount, witness=witness)
    except ValidationError as e:
        return False
    return True

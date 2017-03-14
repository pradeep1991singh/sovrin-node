import pytest
import json

from pyorient import OrientBinaryObject

from anoncreds.protocol.issuer import Issuer
from anoncreds.protocol.repo.attributes_repo import AttributeRepoInMemory
from anoncreds.protocol.types import Schema, ID
from anoncreds.protocol.wallet.issuer_wallet import IssuerWalletInMemory
from anoncreds.test.conftest import GVT

from plenum.common.eventually import eventually
from plenum.common.txn import VERSION
from plenum.common.util import randomString
from plenum.test.test_node import checkNodesConnected
from plenum.test.node_catchup.helper import checkNodeLedgersForEquality

from sovrin_client.test.conftest import primes1
from sovrin_client.anon_creds.sovrin_public_repo import SovrinPublicRepo
from sovrin_client.client.wallet.attribute import Attribute, LedgerStore
from sovrin_client.client.wallet.wallet import Wallet
from sovrin_client.client.client import Client

from plenum.common.txn import TGB
from sovrin_client.test.helper import addRole, getClientAddedWithRole
from sovrin_client.test.conftest import userWalletA

from sovrin_node.test.helper import addAttributeAndCheck
from sovrin_node.test.upgrade.conftest import validUpgrade
from sovrin_node.test.upgrade.helper import checkUpgradeScheduled
from sovrin_node.test.helper import TestNode


@pytest.fixture(scope="module")
def anotherTGB(nodeSet, tdir, looper, trustee, trusteeWallet):
    return getClientAddedWithRole(nodeSet, tdir, looper,
                                  trustee, trusteeWallet,
                                  'newTGB', TGB)


@pytest.fixture(scope="module")
def addNymTxn(looper, anotherTGB):
    """
    Make new NYM transaction
    The new TGB adds a NYM to ledger
    """
    addRole(looper, *anotherTGB, name=randomString())


@pytest.fixture(scope="module")
def addedRawAttribute(userWalletA: Wallet, trustAnchor: Client,
                      trustAnchorWallet: Wallet, looper):
    attrib = Attribute(name='test attribute',
                       origin=trustAnchorWallet.defaultId,
                       value=json.dumps({'name': 'Mario'}),
                       dest=userWalletA.defaultId,
                       ledgerStore=LedgerStore.RAW)
    addAttributeAndCheck(looper, trustAnchor, trustAnchorWallet, attrib)
    return attrib


@pytest.fixture(scope="module")
def publicRepo(steward, stewardWallet):
    return SovrinPublicRepo(steward, stewardWallet)


@pytest.fixture(scope="module")
def schemaDefGvt(stewardWallet):
    return Schema('GVT', '1.0', GVT.attribNames(), 'CL',
                  stewardWallet.defaultId)


@pytest.fixture(scope="module")
def submittedSchemaDefGvt(publicRepo, schemaDefGvt, looper):
    return looper.run(publicRepo.submitSchema(schemaDefGvt))


@pytest.fixture(scope="module")
def submittedPublicKey(submittedPublicKeys):
    return submittedPublicKeys[0]


@pytest.fixture(scope="module")
def issuerGvt(publicRepo):
    return Issuer(IssuerWalletInMemory('issuer1', publicRepo),
                  AttributeRepoInMemory())


@pytest.fixture(scope="module")
def publicSecretKey(submittedSchemaDefGvtID, issuerGvt, primes1, looper):
    return looper.run(
        issuerGvt._primaryIssuer.genKeys(submittedSchemaDefGvtID, **primes1))


@pytest.fixture(scope="module")
def publicSecretRevocationKey(issuerGvt, looper):
    return looper.run(issuerGvt._nonRevocationIssuer.genRevocationKeys())


@pytest.fixture(scope="module")
def submittedSchemaDefGvtID(submittedSchemaDefGvt):
    return ID(schemaKey=submittedSchemaDefGvt.getKey(),
              schemaId=submittedSchemaDefGvt.seqId)


@pytest.fixture(scope="module")
def submittedPublicKeys(submittedSchemaDefGvtID, publicRepo, publicSecretKey,
                        publicSecretRevocationKey, looper):
    pk, sk = publicSecretKey
    pkR, skR = publicSecretRevocationKey
    return looper.run(
        publicRepo.submitPublicKeys(id=submittedSchemaDefGvtID, pk=pk, pkR=pkR))


def compareGraph(table, nodeSet):
    """
    compare stopped node graph(db) with
    other nodes
    """
    stoppedNodeRecords = []
    stoppedNodeClient = nodeSet[0].graphStore.client
    stoppedNodeRecordCount = stoppedNodeClient.db_count_records()

    tableRecodesStoppedNode = stoppedNodeClient.query("SELECT * FROM {}".format(table))
    for nodeRecord in tableRecodesStoppedNode:
        stoppedNodeRecords.append({k: v for k, v in nodeRecord.oRecordData.items()
                                   if not isinstance(v, OrientBinaryObject)
                                   })

    for node in nodeSet[1:4]:
        client = node.graphStore.client
        recordCount = client.db_count_records()
        assert recordCount == stoppedNodeRecordCount

        records = []
        tableRecodes = client.query("SELECT * FROM {}".format(table))
        for record in tableRecodes:
            records.append({k: v for k, v in record.oRecordData.items()
                            if not isinstance(v, OrientBinaryObject)
                            })
        # assert records == stoppedNodeRecords


def testStopFirstNodeAndCleanGraph(addNymTxn, addedRawAttribute, submittedPublicKeys,
                                   nodeSet, looper, tconf, tdirWithPoolTxns,
                                   allPluginsPath, txnPoolNodeSet):
    """
    stop first node (which will clean graph db too)
    restart node
    """
    nodeToStop = nodeSet[0]
    nodeToStop.cleanupOnStopping = False
    nodeToStop.stop()
    looper.removeProdable(nodeToStop)
    client = nodeToStop.graphStore.client
    client.db_drop(client._connection.db_opened)
    newNode = TestNode(nodeToStop.name, basedirpath=tdirWithPoolTxns,
                       config=tconf, pluginPaths=allPluginsPath,
                       ha=nodeToStop.nodestack.ha, cliha=nodeToStop.clientstack.ha)
    looper.add(newNode)
    nodeSet[0] = newNode
    looper.run(checkNodesConnected(nodeSet, overrideTimeout=30))
    looper.run(eventually(checkNodeLedgersForEquality, newNode,
                          *txnPoolNodeSet[1:4], retryWait=1, timeout=15))

    # compareGraph("NYM", nodeSet)
    # compareGraph("Attribute", nodeSet)
    compareGraph("IssuerKey", nodeSet)
    # compareGraph("Schema", nodeSet)


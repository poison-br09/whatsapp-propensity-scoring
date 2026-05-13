import { Boom } from '@hapi/boom'
import P from 'pino'
import makeWASocket, {
    DEFAULT_CONNECTION_CONFIG,
    DisconnectReason,
    fetchLatestBaileysVersion,
    getKeyAuthor,
    jidNormalizedUser,
    makeCacheableSignalKeyStore,
    proto,
    sha256,
    useMultiFileAuthState,
    type ConnectionState,
    type GroupMetadata,
    type WAMessage,
    type WAMessageContent,
    type WAMessageKey,
} from '@whiskeysockets/baileys'

const bridgeLogLevel = process.env.LOG_LEVEL ?? 'info'
const protocolLogLevel = process.env.PROTOCOL_LOG_LEVEL ?? 'error'

const createLogger = (component: string, level: string) =>
	P({
		level,
		base: { service: 'whatsapp-bridge', component },
		timestamp: P.stdTimeFunctions.isoTime,
	})

const logger = createLogger('bridge', bridgeLogLevel)
const protocolLogger = createLogger('baileys', protocolLogLevel)

const targetGroupJidEnv = process.env.TARGET_GROUP_JID?.trim()
const targetGroupNameEnv = process.env.TARGET_GROUP_NAME?.trim()
const pythonInternalBaseUrl = process.env.PYTHON_INTERNAL_BASE_URL ?? 'http://127.0.0.1:8000'
const pythonInternalToken = process.env.PYTHON_INTERNAL_TOKEN
const authDir = process.env.AUTH_DIR ?? 'baileys_auth_info'
const usePairingCode = process.env.USE_PAIRING_CODE === 'true'
const phoneNumber = process.env.PHONE_NUMBER?.trim()

if (!targetGroupJidEnv && !targetGroupNameEnv) {
    throw new Error('Either TARGET_GROUP_JID or TARGET_GROUP_NAME is required')
}

if (usePairingCode && !phoneNumber) {
    throw new Error('PHONE_NUMBER is required when USE_PAIRING_CODE=true')
}

if (!pythonInternalToken) {
    throw new Error('PYTHON_INTERNAL_TOKEN is required')
}

const pollStore = new Map<string, WAMessage>()
const syncedPolls = new Set<string>()

let liveVoteProcessingEnabled = false
let connectionState: Partial<ConnectionState> = {}
let targetGroupJid = targetGroupJidEnv || ''
let pairingCodeRequested = false

const pollKey = (key: WAMessageKey) => `${key.remoteJid ?? ''}:${key.id ?? ''}`
const isTargetGroup = (jid: string | null | undefined) => !!jid && !!targetGroupJid && jid === targetGroupJid

const isPollCreationMessage = (message: WAMessage['message']) =>
    !!(
        message?.pollCreationMessage ||
        message?.pollCreationMessageV2 ||
        message?.pollCreationMessageV3
    )

const getPollTitle = (message: WAMessage['message']) =>
    message?.pollCreationMessage?.name ||
    message?.pollCreationMessageV2?.name ||
    message?.pollCreationMessageV3?.name ||
    ''

const getPollOptions = (message: WAMessage['message']) =>
    message?.pollCreationMessage?.options ||
    message?.pollCreationMessageV2?.options ||
    message?.pollCreationMessageV3?.options ||
    []

const getMessageTimestampMs = (message: WAMessage) => {
    const rawTimestamp = message.messageTimestamp
    if (!rawTimestamp) {
        return Date.now()
    }

    if (typeof rawTimestamp === 'number') {
        return rawTimestamp * 1000
    }

    return Number(rawTimestamp.toString()) * 1000
}

const cachePollCreationMessage = (message: WAMessage) => {
    if (!isTargetGroup(message.key.remoteJid) || !message.key.id || !isPollCreationMessage(message.message)) {
        return
    }

    pollStore.set(pollKey(message.key), message)
    logger.debug(
        { key: message.key.id, pollTitle: getPollTitle(message.message) },
        'cached poll creation message'
    )
}

const getMessage = async (key: WAMessageKey): Promise<WAMessageContent | undefined> => {
    return pollStore.get(pollKey(key))?.message
}

const normalizeVoterPhone = (jid: string) => {
    const normalized = jidNormalizedUser(jid)
    const userPart = normalized.split('@')[0] ?? ''
    return userPart.split(':')[0] ?? ''
}

const resolveSelectedOptionNames = (
    message: WAMessage['message'],
    selectedOptions: Uint8Array[]
) => {
    const optionMap = new Map<string, string>()
    for (const option of getPollOptions(message)) {
        if (!option.optionName) {
            continue
        }

        const hash = sha256(Buffer.from(option.optionName)).toString()
        optionMap.set(hash, option.optionName)
    }

    return selectedOptions.map(option => optionMap.get(Buffer.from(option).toString()) ?? 'Unknown')
}

const postInternal = async (path: string, payload: Record<string, unknown>) => {
    const response = await fetch(`${pythonInternalBaseUrl}${path}`, {
        method: 'POST',
        headers: {
            'content-type': 'application/json',
            'x-internal-token': pythonInternalToken,
        },
        body: JSON.stringify(payload),
    })

    if (!response.ok) {
        const text = await response.text()
        throw new Error(`Python endpoint rejected request: ${response.status} ${text}`)
    }
}

const logKnownGroups = (groups: Record<string, GroupMetadata>) => {
    const entries = Object.entries(groups).map(([jid, metadata]) => ({ jid, subject: metadata.subject }))
    logger.info({ groups: entries }, 'available WhatsApp groups for bridge selection')
}

const resolveTargetGroupJid = async (sock: ReturnType<typeof makeWASocket>) => {
    if (targetGroupJid) {
        return targetGroupJid
    }

    if (!targetGroupNameEnv) {
        return ''
    }

    const groups = await sock.groupFetchAllParticipating()
    logKnownGroups(groups)
    const match = Object.entries(groups).find(([, metadata]) => metadata.subject?.trim() === targetGroupNameEnv)
    if (!match) {
        throw new Error(`Could not find WhatsApp group with subject: ${targetGroupNameEnv}`)
    }

    targetGroupJid = match[0]
    logger.info({ targetGroupName: targetGroupNameEnv, targetGroupJid }, 'resolved target group by name')
    return targetGroupJid
}

const syncPollCreationMessage = async (message: WAMessage) => {
    if (!message.key.id || !isTargetGroup(message.key.remoteJid) || !message.message || !isPollCreationMessage(message.message)) {
        return
    }

    if (syncedPolls.has(message.key.id)) {
        return
    }

    await postInternal('/internal/whatsapp/poll-created', {
        group_jid: targetGroupJid,
        poll_message_id: message.key.id,
        poll_title: getPollTitle(message.message),
        poll_options: getPollOptions(message.message).map(option => option.optionName || ''),
        poll_created_at_ms: getMessageTimestampMs(message),
    })

    syncedPolls.add(message.key.id)
    logger.info({ pollMessageId: message.key.id, pollTitle: getPollTitle(message.message) }, 'synced poll creation to Python service')
}

const processPollUpdates = async (updates: { key: WAMessageKey; update: { pollUpdates?: proto.IPollUpdate[] } }[]) => {
    for (const { key, update } of updates) {
        if (!isTargetGroup(key.remoteJid) || !update.pollUpdates?.length) {
            continue
        }

        if (!liveVoteProcessingEnabled) {
            logger.debug({ pollMessageId: key.id }, 'ignoring poll vote update during history bootstrap')
            continue
        }

        const pollCreationMessage = pollStore.get(pollKey(key))
        if (!pollCreationMessage?.message) {
            logger.warn({ pollMessageId: key.id }, 'poll creation message missing for vote update')
            continue
        }

        for (const pollUpdate of update.pollUpdates) {
            const pollUpdateMessageKey = pollUpdate.pollUpdateMessageKey
            const selectedOptions = pollUpdate.vote?.selectedOptions ?? []
            const voterJid = getKeyAuthor(pollUpdateMessageKey)
            const selectedOptionNames = resolveSelectedOptionNames(pollCreationMessage.message, selectedOptions)
            const dedupeKey =
                pollUpdateMessageKey?.id ||
                `${key.id}:${voterJid}:${pollUpdate.senderTimestampMs ?? Date.now()}:${selectedOptionNames.join('|')}`

            if (!voterJid) {
                logger.warn({ pollMessageId: key.id }, 'poll vote update had no voter jid')
                continue
            }

            await postInternal('/internal/whatsapp/poll-vote', {
                dedupe_key: dedupeKey,
                group_jid: targetGroupJid,
                poll_message_id: key.id,
                poll_title: getPollTitle(pollCreationMessage.message),
                poll_options: getPollOptions(pollCreationMessage.message).map(option => option.optionName || ''),
                voter_jid: voterJid,
                voter_phone: normalizeVoterPhone(voterJid),
                selected_options: selectedOptionNames,
                vote_timestamp_ms: pollUpdate.senderTimestampMs ?? Date.now(),
            })

            logger.info(
                {
                    pollMessageId: key.id,
                    voterJid,
                    selectedOptionNames,
                },
                'forwarded poll vote update to Python service'
            )
        }
    }
}

const startSock = async () => {
    const { state, saveCreds } = await useMultiFileAuthState(authDir)
    const { version, isLatest } = await fetchLatestBaileysVersion()
    logger.info({ version: version.join('.'), isLatest, targetGroupJid, targetGroupNameEnv, usePairingCode }, 'starting poll bridge')

    const sock = makeWASocket({
        version,
        logger: protocolLogger,
        waWebSocketUrl: process.env.SOCKET_URL ?? DEFAULT_CONNECTION_CONFIG.waWebSocketUrl,
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, protocolLogger),
        },
        generateHighQualityLinkPreview: false,
        getMessage,
    })

    sock.ev.process(async events => {
        if (events['connection.update']) {
            const update = events['connection.update']
            connectionState = update

            if (usePairingCode && update.qr && !sock.authState.creds.registered && !pairingCodeRequested && phoneNumber) {
                pairingCodeRequested = true
                const code = await sock.requestPairingCode(phoneNumber)
                logger.info({ phoneNumber, code }, 'WhatsApp pairing code generated')
            }

            if (update.connection === 'open') {
                await resolveTargetGroupJid(sock)
            }

            if (update.receivedPendingNotifications) {
                liveVoteProcessingEnabled = true
                logger.info('history bootstrap complete, live poll processing enabled')
            }

            if (update.connection === 'close') {
                pairingCodeRequested = false
                const statusCode = (update.lastDisconnect?.error as Boom | undefined)?.output?.statusCode
                if (statusCode !== DisconnectReason.loggedOut) {
                    logger.warn({ statusCode }, 'socket closed, reconnecting poll bridge')
                    void startSock()
                } else {
                    logger.fatal('poll bridge session was logged out')
                }
            }
        }

        if (events['creds.update']) {
            await saveCreds()
        }

        if (events['messaging-history.set']) {
            for (const message of events['messaging-history.set'].messages) {
                cachePollCreationMessage(message)
                await syncPollCreationMessage(message)
            }
        }

        if (events['messages.upsert']) {
            for (const message of events['messages.upsert'].messages) {
                cachePollCreationMessage(message)
                await syncPollCreationMessage(message)
            }
        }

        if (events['messages.update']) {
            await processPollUpdates(events['messages.update'])
        }
    })

    return sock
}

await startSock()

process.on('SIGTERM', () => {
    logger.info({ connectionState, targetGroupJid }, 'shutting down poll bridge')
    process.exit(0)
})

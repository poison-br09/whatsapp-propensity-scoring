import { Boom } from '@hapi/boom'
import { writeFile } from 'node:fs/promises'
import P from 'pino'
import makeWASocket, {
    DEFAULT_CONNECTION_CONFIG,
    DisconnectReason,
    fetchLatestBaileysVersion,
    getKeyAuthor,
    jidNormalizedUser,
    makeCacheableSignalKeyStore,
    normalizeMessageContent,
    proto,
    sha256,
    useMultiFileAuthState,
    type ConnectionState,
    type GroupMetadata,
    type WAMessage,
    type WAMessageContent,
    type WAMessageKey,
    type WAMessageUpdate,
} from '@whiskeysockets/baileys'
import { decryptPollVote } from '@whiskeysockets/baileys/lib/Utils/process-message.js'

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
const pairingCodeOutputPath = process.env.PAIRING_CODE_OUTPUT_PATH?.trim()

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
const isGroupJid = (jid: string | null | undefined) => !!jid && jid.endsWith('@g.us')
const isTargetGroup = (jid: string | null | undefined) => {
    if (!isGroupJid(jid)) {
        return false
    }

    if (!targetGroupJid) {
        return true
    }

    return jid === targetGroupJid
}

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
    return pollStore.get(pollKey(key))?.message ?? undefined
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

const getTimestampMs = (
    value: number | { toString(): string } | null | undefined,
    fallback: number
) => {
    if (typeof value === 'number') {
        return value
    }

    if (value && typeof value === 'object' && 'toString' in value) {
        return Number(value.toString())
    }

    return fallback
}

const buildVoteDedupeKey = (
    pollMessageId: string | null | undefined,
    voterJid: string,
    selectedOptionNames: string[],
    timestampMs: number,
    sourceMessageId?: string | null
) =>
    sourceMessageId ||
    `${pollMessageId ?? 'unknown'}:${voterJid}:${timestampMs}:${selectedOptionNames.join('|')}`

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

const reportSessionEvent = async (
    event: string,
    payload: {
        status_code?: number | null
        pairing_required?: boolean
        target_group_jid?: string | null
    } = {}
) => {
    try {
        await postInternal('/internal/whatsapp/session-event', {
            event,
            phone_number: phoneNumber || null,
            status_code: payload.status_code ?? null,
            occurred_at: new Date().toISOString(),
            target_group_jid: payload.target_group_jid ?? (targetGroupJid || null),
            pairing_required: payload.pairing_required ?? false,
        })
    } catch (error) {
        logger.error({ err: error, event }, 'failed to report WhatsApp session event to Python service')
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
        logger.info('no target group configured, processing polls from all WhatsApp groups')
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
        group_jid: message.key.remoteJid,
        poll_message_id: message.key.id,
        poll_title: getPollTitle(message.message),
        poll_options: getPollOptions(message.message).map(option => option.optionName || ''),
        poll_created_at_ms: getMessageTimestampMs(message),
    })

    syncedPolls.add(message.key.id)
    logger.info({ pollMessageId: message.key.id, pollTitle: getPollTitle(message.message) }, 'synced poll creation to Python service')
}

const forwardPollVoteUpdate = async ({
    groupJid,
    pollMessageId,
    pollCreationMessage,
    voterJid,
    selectedOptions,
    timestampMs,
    sourceMessageId,
    source,
}: {
    groupJid: string | null | undefined
    pollMessageId: string | null | undefined
    pollCreationMessage: WAMessage
    voterJid: string
    selectedOptions: Uint8Array[]
    timestampMs: number
    sourceMessageId?: string | null
    source: 'messages.update' | 'messages.upsert'
}) => {
    const selectedOptionNames = resolveSelectedOptionNames(pollCreationMessage.message, selectedOptions)
    const dedupeKey = buildVoteDedupeKey(
        pollMessageId,
        voterJid,
        selectedOptionNames,
        timestampMs,
        sourceMessageId
    )

    await postInternal('/internal/whatsapp/poll-vote', {
        dedupe_key: dedupeKey,
        group_jid: groupJid,
        poll_message_id: pollMessageId,
        poll_title: getPollTitle(pollCreationMessage.message),
        poll_options: getPollOptions(pollCreationMessage.message).map(option => option.optionName || ''),
        voter_jid: voterJid,
        voter_phone: normalizeVoterPhone(voterJid),
        selected_options: selectedOptionNames,
        vote_timestamp_ms: timestampMs,
    })

    logger.info(
        {
            source,
            pollMessageId,
            voterJid,
            selectedOptionNames,
        },
        'forwarded poll vote update to Python service'
    )
}

const processPollUpdates = async (updates: WAMessageUpdate[]) => {
    const pollRelatedUpdates = updates.filter(({ key, update }) => isTargetGroup(key.remoteJid) && !!update.pollUpdates?.length)
    if (pollRelatedUpdates.length) {
        logger.info({ count: pollRelatedUpdates.length }, 'received poll-related messages.update entries')
    }

    for (const { key, update } of updates) {
        if (!isTargetGroup(key.remoteJid)) {
            logger.debug({ pollMessageId: key.id, remoteJid: key.remoteJid }, 'ignoring poll update outside configured groups')
            continue
        }

        if (!update.pollUpdates?.length) {
            logger.debug({ pollMessageId: key.id }, 'ignoring messages.update without pollUpdates')
            continue
        }

        if (!liveVoteProcessingEnabled) {
            logger.debug({ pollMessageId: key.id }, 'ignoring poll vote update during history bootstrap')
            continue
        }

        const pollCreationMessage = pollStore.get(pollKey(key))
        if (!pollCreationMessage?.message) {
            logger.warn({ pollMessageId: key.id, remoteJid: key.remoteJid }, 'poll creation message missing for vote update')
            continue
        }

        for (const pollUpdate of update.pollUpdates) {
            const pollUpdateMessageKey = pollUpdate.pollUpdateMessageKey
            const selectedOptions = pollUpdate.vote?.selectedOptions ?? []
            const voterJid = getKeyAuthor(pollUpdateMessageKey)

            if (!voterJid) {
                logger.warn({ pollMessageId: key.id }, 'poll vote update had no voter jid')
                continue
            }

            await forwardPollVoteUpdate({
                groupJid: key.remoteJid,
                pollMessageId: key.id,
                pollCreationMessage,
                voterJid,
                selectedOptions,
                timestampMs: getTimestampMs(pollUpdate.senderTimestampMs, Date.now()),
                sourceMessageId: pollUpdateMessageKey?.id,
                source: 'messages.update',
            })
        }
    }
}

const processPollVoteMessages = async (messages: WAMessage[]) => {
    const pollUpdateMessages = messages.filter(message => !!normalizeMessageContent(message.message)?.pollUpdateMessage)
    if (pollUpdateMessages.length) {
        logger.info({ count: pollUpdateMessages.length }, 'received poll update messages in upsert batch')
    }

    for (const message of messages) {
        const content = normalizeMessageContent(message.message)
        const pollUpdateMessage = content?.pollUpdateMessage
        if (!pollUpdateMessage) {
            continue
        }

        logger.info(
            {
                messageId: message.key.id,
                remoteJid: message.key.remoteJid,
                pollMessageId: pollUpdateMessage.pollCreationMessageKey?.id,
            },
            'processing incoming poll update message'
        )

        if (!isTargetGroup(message.key.remoteJid)) {
            logger.debug(
                { messageId: message.key.id, remoteJid: message.key.remoteJid },
                'ignoring poll update message outside configured groups'
            )
            continue
        }

        if (!liveVoteProcessingEnabled) {
            logger.debug({ messageId: message.key.id }, 'ignoring poll update message during history bootstrap')
            continue
        }

        const creationMsgKey = pollUpdateMessage.pollCreationMessageKey
        const pollMessageId = creationMsgKey?.id
        if (!creationMsgKey?.remoteJid || !pollMessageId) {
            logger.warn({ messageId: message.key.id }, 'poll update message missing poll creation key details')
            continue
        }

        const pollCreationMessage = pollStore.get(pollKey(creationMsgKey))
        if (!pollCreationMessage?.message) {
            logger.warn(
                { messageId: message.key.id, pollMessageId, remoteJid: creationMsgKey.remoteJid },
                'poll creation message missing for poll update message'
            )
            continue
        }

        const pollEncKey = pollCreationMessage.message.messageContextInfo?.messageSecret
        if (!pollEncKey) {
            logger.warn({ messageId: message.key.id, pollMessageId }, 'poll creation message missing messageSecret')
            continue
        }

        const pollCreatorJid = getKeyAuthor(creationMsgKey)
        const voterJid = getKeyAuthor(message.key)
        if (!pollCreatorJid || !voterJid || !pollUpdateMessage.vote) {
            logger.warn(
                { messageId: message.key.id, pollMessageId, hasVote: !!pollUpdateMessage.vote },
                'poll update message missing decrypt context'
            )
            continue
        }

        let decryptedVote: proto.Message.IPollVoteMessage
        try {
            decryptedVote = decryptPollVote(pollUpdateMessage.vote, {
                pollCreatorJid,
                pollMsgId: pollMessageId,
                pollEncKey,
                voterJid,
            })
        } catch (error) {
            logger.warn({ err: error, messageId: message.key.id, pollMessageId }, 'failed to decrypt poll update message')
            continue
        }

        await forwardPollVoteUpdate({
            groupJid: creationMsgKey.remoteJid,
            pollMessageId,
            pollCreationMessage,
            voterJid,
            selectedOptions: decryptedVote.selectedOptions || [],
            timestampMs: getTimestampMs(pollUpdateMessage.senderTimestampMs, getMessageTimestampMs(message)),
            sourceMessageId: message.key.id,
            source: 'messages.upsert',
        })
    }
}

const startSock = async () => {
    const { state, saveCreds } = await useMultiFileAuthState(authDir)
    const { version, isLatest } = await fetchLatestBaileysVersion()
    logger.info(
        {
            version: version.join('.'),
            isLatest,
            targetGroupJid,
            targetGroupNameEnv,
            processAllGroups: !targetGroupJidEnv && !targetGroupNameEnv,
            usePairingCode,
        },
        'starting poll bridge'
    )
    await reportSessionEvent('starting', { pairing_required: usePairingCode })

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
                await reportSessionEvent('pairing_code_generated', { pairing_required: true })
                if (pairingCodeOutputPath) {
                    await writeFile(
                        pairingCodeOutputPath,
                        JSON.stringify({
                            phoneNumber,
                            code,
                            generatedAt: new Date().toISOString(),
                        }),
                        'utf-8'
                    )
                }
            }

            if (update.connection === 'open') {
                await resolveTargetGroupJid(sock)
                await reportSessionEvent('connected', {
                    pairing_required: false,
                    target_group_jid: targetGroupJid || null,
                })
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
                    await reportSessionEvent('reconnecting', {
                        status_code: statusCode ?? null,
                        pairing_required: false,
                    })
                    void startSock()
                } else {
                    await reportSessionEvent('logged_out', {
                        status_code: statusCode ?? null,
                        pairing_required: true,
                    })
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

            await processPollVoteMessages(events['messages.upsert'].messages)
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

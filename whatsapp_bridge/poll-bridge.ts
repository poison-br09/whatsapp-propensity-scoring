import { Boom } from '@hapi/boom'
import { timingSafeEqual } from 'node:crypto'
import { existsSync } from 'node:fs'
import { readFile, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import P from 'pino'
import makeWASocket, {
    DEFAULT_CONNECTION_CONFIG,
    DisconnectReason,
    fetchLatestBaileysVersion,
    getKeyAuthor,
    jidDecode,
    jidNormalizedUser,
    makeCacheableSignalKeyStore,
    normalizeMessageContent,
    proto,
    sha256,
    useMultiFileAuthState,
    type ConnectionState,
    type Contact,
    type GroupMetadata,
    type LIDMapping,
    type SignalKeyStore,
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
const pollStorePath = process.env.POLL_STORE_PATH ?? 'poll_store.json'
const usePairingCode = process.env.USE_PAIRING_CODE === 'true'
const phoneNumber = process.env.PHONE_NUMBER?.trim()
const pairingCodeOutputPath = process.env.PAIRING_CODE_OUTPUT_PATH?.trim()
const historyBackfillPageSize = Math.max(1, Number(process.env.HISTORY_BACKFILL_PAGE_SIZE ?? '50') || 50)
const historyBackfillMaxPages = Math.max(1, Number(process.env.HISTORY_BACKFILL_MAX_PAGES ?? '3') || 3)
const historyBackfillTimeoutMs = Math.max(1_000, Number(process.env.HISTORY_BACKFILL_TIMEOUT_MS ?? '15000') || 15_000)
let backfillEnabled = process.env.ENABLE_HISTORY_BACKFILL !== 'false'
const backfillControlPort = Math.max(1, Number(process.env.BACKFILL_CONTROL_PORT ?? '8001') || 8001)

if (usePairingCode && !phoneNumber) {
    throw new Error('PHONE_NUMBER is required when USE_PAIRING_CODE=true')
}

if (!pythonInternalToken) {
    throw new Error('PYTHON_INTERNAL_TOKEN is required')
}

const pollStore = new Map<string, WAMessage>()
const syncedPolls = new Set<string>()
const oldestGroupMessages = new Map<string, WAMessage>()
const pendingHistoryBackfills = new Map<string, Promise<void>>()

let liveVoteProcessingEnabled = false
let connectionState: Partial<ConnectionState> = {}
let targetGroupJid = targetGroupJidEnv || ''
let pairingCodeRequested = false
let ownPnJid = ''
let ownLidJid = ''
let authStateKeys: SignalKeyStore | null = null
let historyBackfillStarted = false
let activeSock: ReturnType<typeof makeWASocket> | null = null

const pollKey = (key: WAMessageKey) => `${key.remoteJid ?? ''}:${key.id ?? ''}`
const isGroupJid = (jid: string | null | undefined): jid is string => !!jid && jid.endsWith('@g.us')
const uniqueJids = (...values: Array<string | null | undefined>) => {
    const seen = new Set<string>()
    const result: string[] = []

    for (const value of values) {
        if (!value) {
            continue
        }

        const normalized = jidNormalizedUser(value)
        if (!normalized || seen.has(normalized)) {
            continue
        }

        seen.add(normalized)
        result.push(normalized)
    }

    return result
}

const getAuthorCandidates = (
    key: WAMessageKey | null | undefined,
    meIds: string[] = []
) =>
    uniqueJids(
        key?.participant,
        key ? getKeyAuthor(key) : undefined,
        ...meIds.flatMap(meId => (key ? [getKeyAuthor(key, meId)] : []))
    )

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

const loadPollStore = async () => {
    if (!existsSync(pollStorePath)) {
        return
    }
    try {
        const raw = await readFile(pollStorePath, 'utf-8')
        const entries = JSON.parse(raw) as Array<{ k: string; v: string }>
        for (const { k, v } of entries) {
            const message = proto.WebMessageInfo.decode(Buffer.from(v, 'base64'))
            pollStore.set(k, message as WAMessage)
            considerOldestGroupMessage(message as WAMessage)
        }
        logger.info({ count: pollStore.size, path: pollStorePath }, 'loaded poll store from disk')
    } catch (error) {
        logger.warn({ err: error, path: pollStorePath }, 'failed to load poll store from disk')
    }
}

const considerOldestGroupMessage = (message: WAMessage) => {
    const groupJid = message.key.remoteJid
    if (!isGroupJid(groupJid) || !message.key.id) {
        return
    }

    const currentOldest = oldestGroupMessages.get(groupJid)
    if (!currentOldest) {
        oldestGroupMessages.set(groupJid, message)
        return
    }

    const candidateTimestamp = getMessageTimestampMs(message)
    const currentTimestamp = getMessageTimestampMs(currentOldest)
    if (candidateTimestamp < currentTimestamp) {
        oldestGroupMessages.set(groupJid, message)
        return
    }

    if (candidateTimestamp === currentTimestamp) {
        const candidateId = message.key.id ?? ''
        const currentId = currentOldest.key.id ?? ''
        if (candidateId && (!currentId || candidateId < currentId)) {
            oldestGroupMessages.set(groupJid, message)
        }
    }
}

const savePollStore = async () => {
    try {
        const entries = Array.from(pollStore.entries()).map(([k, message]) => ({
            k,
            v: Buffer.from(proto.WebMessageInfo.encode(message).finish()).toString('base64'),
        }))
        await writeFile(pollStorePath, JSON.stringify(entries), 'utf-8')
    } catch (error) {
        logger.warn({ err: error, path: pollStorePath }, 'failed to save poll store to disk')
    }
}

const cachePollCreationMessage = (message: WAMessage) => {
    considerOldestGroupMessage(message)

    if (!isTargetGroup(message.key.remoteJid) || !message.key.id || !isPollCreationMessage(message.message)) {
        return
    }

    pollStore.set(pollKey(message.key), message)
    logger.debug(
        { key: message.key.id, pollTitle: getPollTitle(message.message) },
        'cached poll creation message'
    )
    void savePollStore()
}

const getMessage = async (key: WAMessageKey): Promise<WAMessageContent | undefined> => {
    return pollStore.get(pollKey(key))?.message ?? undefined
}

const getKeyParticipantAlt = (key: WAMessageKey | proto.IMessageKey | null | undefined) =>
    (key as { participantAlt?: string } | null | undefined)?.participantAlt

const normalizeVoterPhone = (jid: string | null | undefined, phoneHint?: string | null) => {
    const preferred = phoneHint ? jidNormalizedUser(phoneHint) : ''
    if (preferred.endsWith('@s.whatsapp.net') || preferred.endsWith('@c.us')) {
        const userPart = preferred.split('@')[0] ?? ''
        return userPart.split(':')[0] ?? ''
    }

    const normalized = jid ? jidNormalizedUser(jid) : ''
    if (!normalized.endsWith('@s.whatsapp.net') && !normalized.endsWith('@c.us')) {
        return null
    }

    const userPart = normalized.split('@')[0] ?? ''
    return userPart.split(':')[0] ?? ''
}

const resolvePhoneHintFromLidMapping = async (jid: string | null | undefined) => {
    if (!authStateKeys || !jid) {
        return null
    }

    const normalized = jidNormalizedUser(jid)
    if (!normalized.endsWith('@lid') && !normalized.endsWith('@hosted.lid')) {
        return null
    }

    const decoded = jidDecode(normalized)
    if (!decoded?.user) {
        return null
    }

    const reverseKey = `${decoded.user}_reverse`
    const stored = await authStateKeys.get('lid-mapping', [reverseKey])
    const pnUser = stored[reverseKey]
    if (!pnUser || typeof pnUser !== 'string') {
        return null
    }

    const deviceSuffix = decoded.device ? `:${decoded.device}` : ''
    return `${pnUser}${deviceSuffix}@s.whatsapp.net`
}

const resolveVoterPhone = async (voterJid: string, voterPhoneHint?: string | null) => {
    const directPhone = normalizeVoterPhone(voterJid, voterPhoneHint)
    if (directPhone) {
        return directPhone
    }

    if (ownLidJid && jidNormalizedUser(voterJid) === ownLidJid) {
        return normalizeVoterPhone(ownPnJid)
    }

    const mappedPhoneHint = await resolvePhoneHintFromLidMapping(voterJid)
    return normalizeVoterPhone(voterJid, mappedPhoneHint)
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

const describeMessageContent = (message: WAMessage['message']) => {
    const normalized = normalizeMessageContent(message)
    if (!normalized) {
        return []
    }

    return Object.keys(normalized).filter(key => normalized[key as keyof typeof normalized] != null)
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
        phone_number?: string | null
    } = {}
) => {
    try {
        await postInternal('/internal/whatsapp/session-event', {
            event,
            phone_number: payload.phone_number ?? (phoneNumber || null),
            status_code: payload.status_code ?? null,
            occurred_at: new Date().toISOString(),
            target_group_jid: payload.target_group_jid ?? (targetGroupJid || null),
            pairing_required: payload.pairing_required ?? false,
        })
    } catch (error) {
        logger.error({ err: error, event }, 'failed to report WhatsApp session event to Python service')
    }
}

const waitForOnDemandHistory = async (
    sock: ReturnType<typeof makeWASocket>,
    requestId: string,
    groupJid: string
) =>
    new Promise<WAMessage[] | null>(resolve => {
        const cleanup = () => {
            clearTimeout(timer)
            sock.ev.off('messaging-history.set', handler)
        }

        const timer = setTimeout(() => {
            cleanup()
            resolve(null)
        }, historyBackfillTimeoutMs)

        const handler = (payload: {
            messages: WAMessage[]
            syncType?: proto.HistorySync.HistorySyncType | null
            peerDataRequestSessionId?: string | null
        }) => {
            if (
                payload.syncType !== proto.HistorySync.HistorySyncType.ON_DEMAND ||
                payload.peerDataRequestSessionId !== requestId
            ) {
                return
            }

            cleanup()
            resolve(payload.messages.filter(message => message.key.remoteJid === groupJid))
        }

        sock.ev.on('messaging-history.set', handler)
    })

const backfillGroupHistoryFromAnchor = async (
    sock: ReturnType<typeof makeWASocket>,
    anchorMessage: WAMessage,
    reason: string
) => {
    const groupJid = anchorMessage.key.remoteJid
    const anchorMessageId = anchorMessage.key.id
    if (!backfillEnabled || !groupJid || !anchorMessageId || !isTargetGroup(groupJid)) {
        return
    }

    const existing = pendingHistoryBackfills.get(groupJid)
    if (existing) {
        await existing
        return
    }

    const backfillPromise = (async () => {
        let previousAnchorId = ''

        for (let page = 1; page <= historyBackfillMaxPages; page += 1) {
            const currentAnchor = oldestGroupMessages.get(groupJid) ?? anchorMessage
            const currentAnchorId = currentAnchor.key.id
            if (!currentAnchorId || currentAnchorId === previousAnchorId) {
                break
            }

            previousAnchorId = currentAnchorId
            const oldestMsgTimestamp =
                currentAnchor.messageTimestamp ?? Math.floor(getMessageTimestampMs(currentAnchor) / 1000)
            const requestId = await sock.fetchMessageHistory(
                historyBackfillPageSize,
                currentAnchor.key,
                oldestMsgTimestamp
            )

            logger.info(
                {
                    groupJid,
                    anchorMessageId: currentAnchorId,
                    page,
                    reason,
                    requestId,
                },
                'requested on-demand group history backfill'
            )

            const historyMessages = await waitForOnDemandHistory(sock, requestId, groupJid)
            if (!historyMessages?.length) {
                logger.info({ groupJid, page, reason, requestId }, 'no additional on-demand history returned')
                break
            }

            const oldestAfterBackfill = oldestGroupMessages.get(groupJid)
            if (!oldestAfterBackfill?.key.id || oldestAfterBackfill.key.id === currentAnchorId) {
                logger.info(
                    {
                        groupJid,
                        page,
                        reason,
                        requestId,
                    },
                    'on-demand history did not advance oldest known message'
                )
                break
            }
        }
    })()

    pendingHistoryBackfills.set(groupJid, backfillPromise)
    try {
        await backfillPromise
    } finally {
        pendingHistoryBackfills.delete(groupJid)
    }
}

const backfillKnownGroupHistory = async (sock: ReturnType<typeof makeWASocket>) => {
    if (!backfillEnabled) {
        logger.info('historical poll backfill disabled by configuration')
        return
    }

    const groupsToBackfill = Array.from(oldestGroupMessages.values())
        .map(message => ({ message, groupJid: message.key.remoteJid }))
        .filter(
            (entry): entry is { message: WAMessage; groupJid: string } =>
                isTargetGroup(entry.groupJid)
        )
    if (!groupsToBackfill.length) {
        logger.info('no known group history anchors available for poll backfill')
        return
    }

    logger.info(
        {
            groups: groupsToBackfill.map(({ message, groupJid }) => ({
                groupJid,
                anchorMessageId: message.key.id,
            })),
            maxPages: historyBackfillMaxPages,
            pageSize: historyBackfillPageSize,
        },
        'starting historical group poll backfill'
    )

    for (const { message } of groupsToBackfill) {
        await backfillGroupHistoryFromAnchor(sock, message, 'initial bootstrap backfill')
    }
}

const recoverMissingPollCreationMessage = async (
    sock: ReturnType<typeof makeWASocket>,
    anchorMessage: WAMessage,
    pollCreationKey: WAMessageKey
) => {
    if (!pollCreationKey.remoteJid || !pollCreationKey.id) {
        return null
    }

    await backfillGroupHistoryFromAnchor(sock, anchorMessage, 'recover missing poll creation')
    return pollStore.get(pollKey(pollCreationKey)) ?? null
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

const syncPollCreationMessage = async (
    message: WAMessage,
    source: 'messages.upsert' | 'messaging-history.set'
) => {
    const groupJid = message.key.remoteJid
    if (!message.key.id || !isTargetGroup(groupJid) || !message.message || !isPollCreationMessage(message.message)) {
        return
    }

    if (syncedPolls.has(message.key.id)) {
        logger.debug(
            {
                source,
                pollMessageId: message.key.id,
                remoteJid: groupJid,
            },
            'skipping already-synced poll creation message'
        )
        return
    }

    logger.info(
        {
            source,
            pollMessageId: message.key.id,
            remoteJid: groupJid,
            pollTitle: getPollTitle(message.message),
            optionCount: getPollOptions(message.message).length,
        },
        'detected poll creation message for sync'
    )

    await postInternal('/internal/whatsapp/poll-created', {
        group_jid: groupJid,
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
    voterPhoneHint,
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
    voterPhoneHint?: string | null
}) => {
    const selectedOptionNames = resolveSelectedOptionNames(pollCreationMessage.message, selectedOptions)
    const dedupeKey = buildVoteDedupeKey(
        pollMessageId,
        voterJid,
        selectedOptionNames,
        timestampMs,
        sourceMessageId
    )
    const voterPhone = await resolveVoterPhone(voterJid, voterPhoneHint)

    await postInternal('/internal/whatsapp/poll-vote', {
        dedupe_key: dedupeKey,
        group_jid: groupJid,
        poll_message_id: pollMessageId,
        poll_title: getPollTitle(pollCreationMessage.message),
        poll_options: getPollOptions(pollCreationMessage.message).map(option => option.optionName || ''),
        voter_jid: voterJid,
        voter_phone: voterPhone,
        selected_options: selectedOptionNames,
        vote_timestamp_ms: timestampMs,
    })

    logger.info(
        {
            source,
            pollMessageId,
            voterJid,
            voterPhone,
            selectedOptionNames,
        },
        'forwarded poll vote update to Python service'
    )
}

const processPollUpdates = async (
    sock: ReturnType<typeof makeWASocket>,
    updates: WAMessageUpdate[]
) => {
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

        for (const pollUpdate of update.pollUpdates) {
            const pollUpdateMessageKey = pollUpdate.pollUpdateMessageKey
            const selectedOptions = pollUpdate.vote?.selectedOptions ?? []
            const voterJid = getKeyAuthor(pollUpdateMessageKey)
            const pollCreationMessage =
                pollStore.get(pollKey(key)) ??
                (pollUpdateMessageKey
                    ? await recoverMissingPollCreationMessage(sock, {
                        key: pollUpdateMessageKey,
                        messageTimestamp: pollUpdate.senderTimestampMs ?? undefined,
                    } as WAMessage, key)
                    : null)

            if (!pollCreationMessage?.message) {
                logger.warn({ pollMessageId: key.id, remoteJid: key.remoteJid }, 'poll creation message missing for vote update')
                continue
            }

            if (!voterJid) {
                logger.warn({ pollMessageId: key.id }, 'poll vote update had no voter jid')
                continue
            }

            await forwardPollVoteUpdate({
                groupJid: key.remoteJid,
                pollMessageId: key.id,
                pollCreationMessage,
                voterJid,
                voterPhoneHint: getKeyParticipantAlt(pollUpdateMessageKey),
                selectedOptions,
                timestampMs: getTimestampMs(pollUpdate.senderTimestampMs, Date.now()),
                sourceMessageId: pollUpdateMessageKey?.id,
                source: 'messages.update',
            })
        }
    }
}

const processPollVoteMessages = async (
    sock: ReturnType<typeof makeWASocket>,
    messages: WAMessage[]
) => {
    const pollUpdateMessages = messages.filter(message => !!normalizeMessageContent(message.message)?.pollUpdateMessage)
    if (pollUpdateMessages.length) {
        logger.info(
            {
                count: pollUpdateMessages.length,
                fromGroups: pollUpdateMessages.filter(m => isGroupJid(m.key.remoteJid)).length,
                remoteJids: pollUpdateMessages.map(m => m.key.remoteJid),
            },
            'received poll update messages in upsert batch'
        )
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
            logger.warn(
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

        const pollCreationMessage =
            pollStore.get(pollKey(creationMsgKey)) ??
            await recoverMissingPollCreationMessage(sock, message, creationMsgKey)

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

        const ownJidCandidates = uniqueJids(ownPnJid, ownLidJid)
        const pollCreatorCandidates = getAuthorCandidates(creationMsgKey, ownJidCandidates)
        const voterCandidates = getAuthorCandidates(message.key, ownJidCandidates)
        if (!pollCreatorCandidates.length || !voterCandidates.length || !pollUpdateMessage.vote) {
            logger.warn(
                {
                    messageId: message.key.id,
                    pollMessageId,
                    hasVote: !!pollUpdateMessage.vote,
                    pollCreatorCandidates,
                    voterCandidates,
                },
                'poll update message missing decrypt context'
            )
            continue
        }

        let decryptedVote: proto.Message.IPollVoteMessage | undefined
        let resolvedPollCreatorJid = ''
        let resolvedVoterJid = ''
        let lastDecryptError: unknown

        for (const pollCreatorJid of pollCreatorCandidates) {
            for (const voterJid of voterCandidates) {
                try {
                    decryptedVote = decryptPollVote(pollUpdateMessage.vote, {
                        pollCreatorJid,
                        pollMsgId: pollMessageId,
                        pollEncKey,
                        voterJid,
                    })
                    resolvedPollCreatorJid = pollCreatorJid
                    resolvedVoterJid = voterJid
                    break
                } catch (error) {
                    lastDecryptError = error
                }
            }

            if (decryptedVote) {
                break
            }
        }

        if (!decryptedVote || !resolvedVoterJid) {
            logger.warn(
                {
                    err: lastDecryptError,
                    messageId: message.key.id,
                    pollMessageId,
                    pollCreatorCandidates,
                    voterCandidates,
                },
                'failed to decrypt poll update message'
            )
            continue
        }

        await forwardPollVoteUpdate({
            groupJid: creationMsgKey.remoteJid,
            pollMessageId,
            pollCreationMessage,
            voterJid: resolvedVoterJid,
            voterPhoneHint: getKeyParticipantAlt(message.key),
            selectedOptions: decryptedVote.selectedOptions || [],
            timestampMs: getTimestampMs(pollUpdateMessage.senderTimestampMs, getMessageTimestampMs(message)),
            sourceMessageId: message.key.id,
            source: 'messages.upsert',
        })
    }
}

const tokenMatches = (a: string, b: string): boolean => {
    const ab = Buffer.from(a)
    const bb = Buffer.from(b)
    return ab.length === bb.length && timingSafeEqual(ab, bb)
}

const getTextContent = (message: WAMessage): string | null => {
    const content = normalizeMessageContent(message.message)
    if (!content) return null
    if (content.conversation) return content.conversation
    if (content.extendedTextMessage?.text) return content.extendedTextMessage.text
    return null
}

type ContactLookup = Map<string, { phone: string | null; name: string | null }>

const buildContactLookup = (contacts: Contact[], lidPnMappings?: LIDMapping[]): ContactLookup => {
    const map: ContactLookup = new Map()
    const lidToPhone = new Map<string, string>()
    for (const m of lidPnMappings ?? []) {
        if (m.lid && m.pn) lidToPhone.set(jidNormalizedUser(m.lid), normalizeVoterPhone(m.pn) ?? m.pn)
    }
    for (const c of contacts) {
        const name = c.notify ?? c.name ?? null
        const phone = normalizeVoterPhone(c.phoneNumber ?? '')
            ?? normalizeVoterPhone(c.id ?? '')
            ?? (c.lid ? lidToPhone.get(jidNormalizedUser(c.lid)) : undefined)
            ?? (c.id ? lidToPhone.get(jidNormalizedUser(c.id)) : undefined)
            ?? null
        const entry = { phone, name }
        if (c.id) map.set(jidNormalizedUser(c.id), entry)
        if (c.lid) map.set(jidNormalizedUser(c.lid), entry)
        if (c.phoneNumber) map.set(jidNormalizedUser(c.phoneNumber), entry)
    }
    return map
}

const forwardChatMessages = async (
    messages: WAMessage[],
    sock: ReturnType<typeof makeWASocket>,
    contactLookup?: ContactLookup,
) => {
    const rawOwnJid = ownPnJid || sock.authState.creds.me?.id || ''
    const receiverPhone = normalizeVoterPhone(rawOwnJid) ?? null
    for (const message of messages) {
        const groupJid = message.key.remoteJid
        if (!isTargetGroup(groupJid) || message.key.fromMe) continue

        const text = getTextContent(message)
        if (!text || !message.key.id) continue

        const senderJid = message.key.participant ?? getKeyParticipantAlt(message.key) ?? ''
        const contactInfo = senderJid ? contactLookup?.get(jidNormalizedUser(senderJid)) : undefined
        const senderPhone = await resolveVoterPhone(senderJid) ?? contactInfo?.phone ?? null
        const senderName = message.pushName ?? contactInfo?.name ?? null
        const timestampMs = getMessageTimestampMs(message)

        try {
            await postInternal('/internal/whatsapp/message', {
                group_jid: groupJid,
                sender_jid: senderJid,
                sender_name: senderName,
                sender_phone: senderPhone,
                receiver_phone: receiverPhone,
                message: text,
                message_id: message.key.id,
                message_timestamp_ms: timestampMs,
            })
        } catch (error) {
            logger.warn(
                { err: error, messageId: message.key.id, groupJid },
                'failed to forward chat message for keyword analysis'
            )
        }
    }
}

const startBackfillControlServer = () => {
    const server = createServer((req, res) => {
        res.setHeader('Content-Type', 'application/json')

        const token = req.headers['x-control-token']
        if (
            typeof token !== 'string' ||
            !pythonInternalToken ||
            !tokenMatches(token, pythonInternalToken)
        ) {
            res.writeHead(401)
            res.end(JSON.stringify({ error: 'Unauthorized' }))
            return
        }

        if (req.method === 'POST' && req.url === '/backfill/start') {
            if (!activeSock) {
                res.writeHead(503)
                res.end(JSON.stringify({ error: 'Bridge socket not connected' }))
                return
            }
            backfillEnabled = true
            historyBackfillStarted = false
            void backfillKnownGroupHistory(activeSock)
            res.writeHead(200)
            res.end(JSON.stringify({ action: 'start', accepted: true }))
        } else if (req.method === 'POST' && req.url === '/backfill/stop') {
            backfillEnabled = false
            res.writeHead(200)
            res.end(JSON.stringify({ action: 'stop', accepted: true }))
        } else {
            res.writeHead(404)
            res.end(JSON.stringify({ error: 'Not found' }))
        }
    })

    server.listen(backfillControlPort, '127.0.0.1', () => {
        logger.info({ port: backfillControlPort }, 'backfill control server listening')
    })
}

const startSock = async () => {
    await loadPollStore()
    const { state, saveCreds } = await useMultiFileAuthState(authDir)
    authStateKeys = state.keys
    if (state.creds.me?.id) {
        ownPnJid = jidNormalizedUser(state.creds.me.id)
    }
    if (state.creds.me?.lid) {
        ownLidJid = jidNormalizedUser(state.creds.me.lid)
    }
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
    activeSock = sock

    sock.ws.on('CB:message', (node: { attrs: Record<string, string> }) => {
        const from = node.attrs.from
        if (!isGroupJid(from)) {
            return
        }

        logger.info(
            {
                rawFrom: from,
                rawId: node.attrs.id,
                rawType: node.attrs.type,
                rawParticipant: node.attrs.participant,
                rawTimestamp: node.attrs.t,
            },
            'received raw group message node'
        )
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
                ownPnJid = sock.authState.creds.me?.id ? jidNormalizedUser(sock.authState.creds.me.id) : ownPnJid
                ownLidJid = sock.authState.creds.me?.lid ? jidNormalizedUser(sock.authState.creds.me.lid) : ownLidJid
                logger.info({ ownPnJid, ownLidJid }, 'own jids resolved on connection open')
                await resolveTargetGroupJid(sock)
                await reportSessionEvent('connected', {
                    phone_number: normalizeVoterPhone(ownPnJid),
                    pairing_required: false,
                    target_group_jid: targetGroupJid || null,
                })
            }

            if (update.receivedPendingNotifications) {
                liveVoteProcessingEnabled = true
                logger.info('history bootstrap complete, live poll processing enabled')
                if (!historyBackfillStarted) {
                    historyBackfillStarted = true
                    void backfillKnownGroupHistory(sock)
                }
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
            const historyPollMessages = events['messaging-history.set'].messages.filter(
                message => isTargetGroup(message.key.remoteJid) && isPollCreationMessage(message.message)
            )
            if (historyPollMessages.length) {
                logger.info(
                    {
                        count: historyPollMessages.length,
                        pollMessageIds: historyPollMessages.map(message => message.key.id),
                    },
                    'received poll creation messages in history sync'
                )
            }

            for (const message of events['messaging-history.set'].messages) {
                considerOldestGroupMessage(message)
                cachePollCreationMessage(message)
                await syncPollCreationMessage(message, 'messaging-history.set')
            }
            const { contacts, lidPnMappings, messages: historyMessages } = events['messaging-history.set']
            const contactLookup = buildContactLookup(contacts ?? [], lidPnMappings)
            await forwardChatMessages(historyMessages, sock, contactLookup)
        }

        if (events['messages.upsert']) {
            for (const message of events['messages.upsert'].messages) {
                if (isTargetGroup(message.key.remoteJid)) {
                    logger.info(
                        {
                            messageId: message.key.id,
                            remoteJid: message.key.remoteJid,
                            fromMe: message.key.fromMe,
                            participant: message.key.participant,
                            contentTypes: describeMessageContent(message.message),
                        },
                        'received group message in upsert batch'
                    )
                }

                considerOldestGroupMessage(message)
                cachePollCreationMessage(message)
                await syncPollCreationMessage(message, 'messages.upsert')
            }

            await processPollVoteMessages(sock, events['messages.upsert'].messages)
            await forwardChatMessages(events['messages.upsert'].messages, sock)
        }

        if (events['messages.update']) {
            const groupUpdates = events['messages.update'].filter(({ key }) => isTargetGroup(key.remoteJid))
            if (groupUpdates.length) {
                logger.info(
                    {
                        total: events['messages.update'].length,
                        groupUpdates: groupUpdates.length,
                        withPollUpdates: groupUpdates.filter(({ update }) => !!update.pollUpdates?.length).length,
                    },
                    'received messages.update batch'
                )
            }

            await processPollUpdates(sock, events['messages.update'])
        }
    })

    return sock
}

await startSock()
startBackfillControlServer()

process.on('SIGTERM', () => {
    logger.info({ connectionState, targetGroupJid }, 'shutting down poll bridge')
    process.exit(0)
})

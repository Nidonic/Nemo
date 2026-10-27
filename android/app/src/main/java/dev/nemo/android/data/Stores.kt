package dev.nemo.android.data

import dev.nemo.android.agent.Lexical
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.io.File

/** Conversations as JSON files; a flow of summaries for the list and a flow per open conversation. */
class ConversationStore(root: File) {
    private val dir = File(root, "conversations").apply { mkdirs() }
    private val cache = HashMap<String, MutableStateFlow<Conversation>>()
    private val _list = MutableStateFlow(loadAll())
    val list: StateFlow<List<Conversation>> = _list

    private fun file(id: String) = File(dir, "$id.json")

    private fun loadAll(): List<Conversation> = (dir.listFiles { f -> f.extension == "json" } ?: emptyArray())
        .mapNotNull { runCatching { json.decodeFromString<Conversation>(it.readText()) }.getOrNull() }
        .sortedByDescending { it.updated }

    @Synchronized
    fun flow(id: String): StateFlow<Conversation>? {
        cache[id]?.let { return it }
        val f = file(id)
        if (!f.exists()) return null
        val conv = runCatching { json.decodeFromString<Conversation>(f.readText()) }.getOrNull() ?: return null
        return MutableStateFlow(conv).also { cache[id] = it }
    }

    fun get(id: String): Conversation? = flow(id)?.value

    @Synchronized
    fun create(title: String = "New chat", origin: String = "local", id: String = newId("t_")): Conversation {
        val c = Conversation(id = id, title = title, origin = origin)
        save(c)
        return c
    }

    @Synchronized
    fun save(c: Conversation) {
        val updated = c.copy(updated = nowSec())
        file(updated.id).writeText(json.encodeToString(Conversation.serializer(), updated))
        cache.getOrPut(updated.id) { MutableStateFlow(updated) }.value = updated
        _list.value = (listOf(updated) + _list.value.filter { it.id != updated.id }).sortedByDescending { it.updated }
    }

    @Synchronized
    fun update(id: String, block: (Conversation) -> Conversation): Conversation? {
        val c = get(id) ?: return null
        return block(c).also(::save)
    }

    fun append(id: String, msg: Msg) = update(id) { it.copy(messages = it.messages + msg) }

    @Synchronized
    fun delete(id: String) {
        file(id).delete()
        cache.remove(id)
        _list.value = _list.value.filter { it.id != id }
    }
}

/** Atomic facts about the user, ranked by rare-word IDF over words and CJK bigrams. */
class MemoryStore(root: File) {
    private val file = File(root, "memory.json")
    private val _facts = MutableStateFlow(runCatching { json.decodeFromString<List<Fact>>(file.readText()) }.getOrDefault(emptyList()))
    val facts: StateFlow<List<Fact>> = _facts

    @Synchronized
    private fun write(list: List<Fact>) {
        _facts.value = list
        file.writeText(json.encodeToString(kotlinx.serialization.builtins.ListSerializer(Fact.serializer()), list))
    }

    fun add(content: String, category: String = "general"): Fact {
        _facts.value.firstOrNull { it.content.equals(content.trim(), ignoreCase = true) }?.let { return it }
        val f = Fact(content = content.trim(), category = category)
        write(listOf(f) + _facts.value)
        return f
    }

    fun remove(id: String): Boolean {
        val before = _facts.value.size
        write(_facts.value.filter { it.id != id })
        return _facts.value.size != before
    }

    fun search(query: String, limit: Int = 8): List<Fact> = Lexical.rank(query, _facts.value) { it.content }.take(limit)
}

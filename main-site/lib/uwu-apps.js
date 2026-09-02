// /lib/uwu-apps.js
// The rules for writing a row in uwusuite_apps, in one place.
//
// Two callers reach that table now: the Admin Panel through /api/apps, and a
// linked Telegram account through the app_ actions in /api/telegram. Both build
// their insert and their patch here, so a tag list refused in one is refused in
// the other, and neither can drift into writing a column the other does not.
//
// Nothing in here touches Supabase or a session. It takes a request body and a
// user id and returns a plain row, which is what makes it safe to call from
// either handler and easy to reason about.

export const ALLOWED_TAGS = ['tools', 'games', 'bots', 'singapore'];

/** The tag list, or a 400 naming the ones that are not allowed. */
export function normalizeTags(tags) {
    const list = Array.isArray(tags) ? tags : [];
    const invalid = list.filter(t => !ALLOWED_TAGS.includes(t));
    if (invalid.length > 0) {
        throw { status: 400, message: `Invalid tags: ${invalid.join(', ')}` };
    }
    return list;
}

/**
 * The three gallery columns move together, always. The thumbnail is an index
 * into the gallery rather than a free standing URL, so it cannot point at an
 * image the gallery no longer holds.
 */
function galleryColumns(galleryUrls, thumbnailIndex) {
    const gallery = Array.isArray(galleryUrls) ? galleryUrls : [];
    const index = Math.max(0, Math.min(parseInt(thumbnailIndex) || 0, gallery.length - 1));
    return {
        gallery_urls: gallery,
        thumbnail_url: gallery[index] || null,
        thumbnail_index: index
    };
}

/** A complete row for an insert. Title and url are the only required fields. */
export function buildCreate(body, userId) {
    const {
        title, url, description, tags, galleryUrls, thumbnailIndex,
        published, sortOrder, publishedDate
    } = body || {};

    if (!title || !url) throw { status: 400, message: 'title and url are required' };

    return {
        title,
        url,
        description: description || null,
        tags: normalizeTags(tags),
        ...galleryColumns(galleryUrls, thumbnailIndex),
        published: !!published,
        sort_order: typeof sortOrder === 'number' ? sortOrder : 0,
        published_date: publishedDate || null,
        created_by: userId,
        updated_by: userId
    };
}

/**
 * A patch carrying only the keys the caller actually sent. An absent key leaves
 * the column alone, which is what lets a caller flip `published` without
 * knowing anything else about the row.
 */
export function buildPatch(body, userId) {
    const {
        title, url, description, tags, galleryUrls, thumbnailIndex,
        published, sortOrder, publishedDate
    } = body || {};

    const patch = { updated_by: userId };
    if (title !== undefined) patch.title = title;
    if (url !== undefined) patch.url = url;
    if (description !== undefined) patch.description = description || null;
    if (tags !== undefined) patch.tags = normalizeTags(tags);
    if (published !== undefined) patch.published = !!published;
    if (sortOrder !== undefined) patch.sort_order = sortOrder;
    if (publishedDate !== undefined) patch.published_date = publishedDate || null;
    if (galleryUrls !== undefined) Object.assign(patch, galleryColumns(galleryUrls, thumbnailIndex));
    return patch;
}

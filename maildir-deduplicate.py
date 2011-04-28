#!/usr/bin/python

##############################################################################
#
# Copyright (C) 2010-2011 Kevin Deldycke <kevin@deldycke.com>
#                         Adam Spiers <adam@spiers.net>
#
# This program is Free Software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
##############################################################################

"""
  This script compare all mails in a maildir folder and subfolders,
  then delete duplicate mails.  You can give a list of mail headers
  to ignore when comparing mails between each others.  I used this
  script to clean up a messed maildir folder after I move several
  mails from a Lotus Notes database.

  Tested on MacOS X 10.6 with Python 2.6.2.
"""

import os
import re
import sys
import hashlib
from optparse     import OptionParser
from mailbox      import Maildir
from email.parser import Parser
from difflib      import unified_diff

# List of mail headers to ignore when computing the hash of a mail.
# BTW, don't worry: mail header manipulation methods are case-insensitive.
HEADERS_TO_IGNORE = [
  # Unique header generated by Lotus Notes on IMAP transfers
  'x-mimetrack',

  # This could differ e.g. if a mail was saved locally at the time it
  # was sent to a mailing list, a copy of the same mail could come
  # back from the list server with an extra footer attached
  'content-length',
]

# Since we're ignoring the Content-Length header for the reasons
# stated above, we limit the allowed difference between message sizes.
# If this is exceed, a RuntimeError is thrown, because this could
# point to message corruption somewhere.
DEFAULT_SIZE_DIFFERENCE_THRESHOLD = 256 # bytes

# Similarly, we generated unified diffs of duplicates and ensure that
# the diff is not greater than a certain size.
DEFAULT_DIFF_THRESHOLD = 512 # bytes

def parse_args():
    parser = OptionParser(
        usage = '%prog [OPTIONS] [MAILDIR [MAILDIR ...]]',
        description = 'Detect/remove duplicates from maildir folders',
    )

    parser.add_option(
        '-d', '--remove', action = 'store_true',
        help = 'Remove duplicates rather than just list them'
    )
    parser.add_option(
        '-i', '--message-id', action = 'store_true',
        help = 'Use Message-ID header as hash key ' \
               '(not recommended - the default is to compute a digest ' \
               'of the whole header with selected headers removed'
    )
    parser.add_option(
        '-S', '--size-threshold', type = 'int',
        default = DEFAULT_SIZE_DIFFERENCE_THRESHOLD,
        help = 'Specify maximum allowed difference in bytes ' \
               'between size of duplicates. ' \
               'Default is %default; set -1 for no threshold.'
    )
    parser.add_option(
        '-D', '--diff-threshold', type = 'int',
        default = DEFAULT_DIFF_THRESHOLD,
        help = 'Specify maximum allowed size in bytes of unified diff ' \
               'between duplicates. ' \
               'Default is %default; set -1 for no threshold.'
    )

    opts, maildirs = parser.parse_args()

    if len(maildirs) == 0:
        usage_error(parser, "Must specify at least one maildir folder")

    return opts, maildirs

def usage_error(parser, error_msg):
    sys.stderr.write("Error: %s\n\n" % error_msg)
    parser.print_help()
    sys.exit(2)

def computeHashKey(mail, ignored_headers, use_message_id):
  """ This method remove some mail headers before generating a digest of the message
  """
  # Make a local copy of the message to manipulate it. I haven't found
  # a cleaner way than passing through a intermediate string
  # representation.
  for header in mail.keys():
    if header.lower() in HEADERS_TO_IGNORE \
    or header.lower().startswith("x-offlineimap-"):
      #show_progress("  ignoring header '%s'" % header)
      del mail[header]

  if use_message_id:
    return mail.get('Message-ID')

  #header_text, sep, payload = mail.as_string().partition("\n\n")
  header_text = '\n'.join('%s: %s' % header for header in mail.items())
  #print header_text

  return hashlib.sha224(header_text).hexdigest()

def collateFolderByHash(mails_by_hash, mail_folder, use_message_id):
  mail_count = 0
  sys.stderr.write("Processing %s mails in the %r folder " % \
                     (len(mail_folder), mail_folder._path))
  for mail_id, message in mail_folder.iteritems():
    mail_hash = computeHashKey(message, HEADERS_TO_IGNORE, use_message_id)
    if mail_count > 0 and mail_count % 100 == 0:
      sys.stderr.write(".")
    #show_progress("  Hash is %s for mail %r" % (mail_hash, mail_id))
    if mail_hash not in mails_by_hash:
      mails_by_hash[mail_hash] = [ ]

    mail_file = os.path.join(mail_folder._path, mail_folder._lookup(mail_id))
    mails_by_hash[mail_hash].append((mail_file, message))
    mail_count += 1

  sys.stderr.write("\n")

  return mail_count

def findDuplicates(mails_by_hash, opts):
  duplicates = 0
  for hash_key, messages in mails_by_hash.iteritems():
    if len(messages) > 1:
      subject = messages[0][1].get('Subject')
      subject, count = re.subn('\s+', ' ', subject)
      print "\n" + subject
      duplicates += len(messages) - 1
      sizes = sort_messages_by_size(messages)
      checkMessagesSimilar(hash_key, sizes, opts.diff_threshold)
      checkSizesComparable(hash_key, sizes, opts.size_threshold)
      i = 0
      for size, mail_file, message in sizes:
        i += 1
        prefix = "  "
        if opts.remove:
          if i > 1:
            prefix = "removed"
            os.unlink(mail_file)
          else:
            prefix = "left   "
        print "%s %2d %d %s" % (prefix, i, size, mail_file)
    # else:
    #   print "unique:", messages[0]

  return duplicates

def sort_messages_by_size(messages):
  sizes = [ ]
  for mail_file, message in messages:
    size = os.path.getsize(mail_file)
    sizes.append((size, mail_file, message))
  def _sort_by_size(a, b):
    return cmp(a[0], b[0])
  sizes.sort(cmp = _sort_by_size)
  return sizes

def checkSizesComparable(hash_key, sizes, threshold):
  if threshold < 0:
    return

  smallest_size, smallest_file, smallest_message = sizes[0]

  for size, mail_file, message in sizes[1:]:
    size_difference = size - smallest_size
    if size_difference > threshold:
      msg = "\nERROR: for hash key %s, sizes differ by %d > %d bytes:\n" \
            "  %d %s\n  %d %s\nAborting.\n" % \
        (hash_key, size_difference, threshold,
         size, mail_file,
         smallest_size, smallest_file)
      sys.stderr.write(msg)
      sys.exit(2)
    # else:
    #   show_progress(
    #     "%s was %d bytes long which is less than %d bytes longer than %s at %d. " %
    #     (mail_file, size, threshold, smallest_file, smallest_size)
    #   )

def getLinesFromFile(path):
  f = open(path)
  lines = f.readlines()
  f.close()
  return lines

def checkMessagesSimilar(hash_key, sizes, threshold):
  if threshold < 0:
    return

  smallest_size, smallest_file, smallest_message = sizes[0]
  smallest_lines = smallest_message.as_string().splitlines(True)

  for size, mail_file, message in sizes[1:]:
    lines = message.as_string().splitlines(True)
    diff = unified_diff(smallest_lines, lines,
                        fromfile = smallest_file,
                        tofile   = mail_file,
                        fromfiledate = os.path.getmtime(smallest_file),
                        tofiledate = os.path.getmtime(mail_file),
                        n = 0, lineterm = "\n")
    difftext = "".join(diff)
    if len(difftext) > threshold:
      msg = ("\nERROR: diff between duplicate messages with hash key %s " % hash_key) + \
          "was too big; aborting!\n\n" + difftext
      sys.stderr.write(msg)
      sys.exit(1)
    # elif len(difftext) == 0:
    #   show_progress("diff produced no differences")
    # else:
    #   show_progress("diff between duplicate messages was small:\n" + difftext)

def show_progress(msg):
  sys.stderr.write(msg + "\n")

def main():
  opts, maildir_paths = parse_args()

  mails_by_hash = { }
  mail_count = 0

  for maildir_path in maildir_paths:
    maildir = Maildir(maildir_path, factory = None)
    mail_count += collateFolderByHash(mails_by_hash, maildir, opts.message_id)

  duplicates = findDuplicates(mails_by_hash, opts)
  show_progress("\n%s duplicates in a total of %s mails." % \
                  (duplicates, mail_count))

main()
